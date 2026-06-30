"""Temp-file store for uploaded datasets.

Refs are UUID4 strings. Every ref maps to a single file under a configurable
temp dir. Active runs lease refs so cleanup never removes data an in-flight
run still needs.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Default temp dir — override by setting INGESTION_TEMP_DIR env var.
_DEFAULT_TEMP_DIR = Path(os.environ.get("INGESTION_TEMP_DIR", "/tmp/ds_agent_uploads"))

# Seconds before an unleased upload is eligible for cleanup.
_DEFAULT_TTL_SECONDS = int(os.environ.get("INGESTION_TTL_SECONDS", str(3 * 60 * 60)))  # 3 h

_lock = threading.Lock()

# {ref: {"path": Path, "mtime": float, "leases": set[run_id]}}
_registry: dict[str, dict] = {}


def _temp_dir() -> Path:
    d = _DEFAULT_TEMP_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(data: bytes, filename: str) -> str:
    """Write *data* to a new temp file and return its opaque ref (UUID4 str)."""
    ref = str(uuid.uuid4())
    temp_dir = _temp_dir()
    # Store under <ref>/<original_filename> so the original name is preserved.
    dest_dir = temp_dir / ref
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(filename).name
    dest.write_bytes(data)
    with _lock:
        _registry[ref] = {
            "path": dest,
            "filename": Path(filename).name,
            "mtime": time.time(),
        }
        _registry[ref].setdefault("leases", set())
    return ref


def path_for(ref: str) -> Path:
    """Return the filesystem path for *ref*.

    Raises:
        ValueError: if ref is not a well-formed UUID4.
        FileNotFoundError: if the ref is unknown or the file is gone.
        PermissionError: if the resolved path escapes the temp dir
            (path-traversal guard).
    """
    # Validate ref is a well-formed UUID (rejects path-traversal strings like
    # '../../etc/passwd' at the identifier level before any path ops).
    try:
        uuid.UUID(ref)
    except ValueError:
        raise ValueError(f"Invalid dataset ref: {ref!r}")

    with _lock:
        entry = _registry.get(ref)
    if entry is None:
        raise FileNotFoundError(f"Unknown dataset ref: {ref!r}")

    path = entry["path"]
    if not path.exists():
        raise FileNotFoundError(f"Temp file missing for ref {ref!r}: {path}")

    # Path-traversal containment: realpath resolves symlinks and '..' segments.
    real_path = os.path.realpath(path)
    real_temp = os.path.realpath(_temp_dir())
    if os.path.commonpath([real_path, real_temp]) != real_temp:
        raise PermissionError(
            f"Ref {ref!r} resolved outside temp dir — possible path traversal"
        )

    # Reset TTL on access.
    with _lock:
        if ref in _registry:
            _registry[ref]["mtime"] = time.time()

    return path


def filename_for(ref: str) -> str:
    """Return the original upload filename for *ref*."""
    path_for(ref)  # validates ref + containment
    with _lock:
        return _registry[ref]["filename"]


def lease(ref: str, run_id: str) -> None:
    """Mark *ref* as in-use by *run_id*; prevents cleanup_expired from deleting it."""
    path_for(ref)  # validates ref exists + containment
    with _lock:
        _registry[ref]["leases"].add(run_id)
        _registry[ref]["mtime"] = time.time()


def release(ref: str, run_id: str) -> None:
    """Remove the lease held by *run_id* for *ref*.

    Safe to call even if the run_id never leased ref.
    """
    with _lock:
        entry = _registry.get(ref)
        if entry is not None:
            entry["leases"].discard(run_id)


def cleanup_expired(ttl_seconds: Optional[int] = None) -> list[str]:
    """Delete temp files whose TTL has expired AND which carry no active leases.

    Returns the list of refs that were removed.
    """
    if ttl_seconds is None:
        ttl_seconds = _DEFAULT_TTL_SECONDS
    cutoff = time.time() - ttl_seconds
    removed: list[str] = []

    with _lock:
        candidates = list(_registry.items())

    for ref, entry in candidates:
        # Never delete while a run holds a lease.
        if entry.get("leases"):
            continue
        if entry["mtime"] < cutoff:
            path: Path = entry["path"]
            try:
                if path.exists():
                    path.unlink()
                # Remove the ref sub-dir if empty.
                parent = path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                # Best-effort; don't abort the sweep.
                pass
            with _lock:
                _registry.pop(ref, None)
            removed.append(ref)

    return removed
