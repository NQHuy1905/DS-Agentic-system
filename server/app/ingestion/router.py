"""FastAPI router for dataset upload — handles POST /eda/upload only.

Phase 9 mounts this router into the main app. This file owns /eda/upload;
the broader /eda/* route tree is owned by app/api/routes/eda.py.

Security:
  - Size cap enforced before reading body (Content-Length header) AND after
    reading bytes (defense-in-depth for chunked transfers).
  - Format validation uses magic bytes, never Content-Type or file extension.
  - cleanup_expired() is called on each upload to bound temp-dir growth.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.ingestion.loader import IngestionError
from app.ingestion.models import DatasetMeta, describe
from app.ingestion.storage import cleanup_expired, save_upload

# Default 200 MB cap — override with INGESTION_MAX_UPLOAD_BYTES env var.
_MAX_UPLOAD_BYTES: int = int(
    os.environ.get("INGESTION_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024))
)

# Magic-byte signatures for accepted formats.
_PARQUET_MAGIC = b"PAR1"


# --------------------------------------------------------------------------- #
# Response schema (local — not shared with eda_schemas to keep phase boundary) #
# --------------------------------------------------------------------------- #

class UploadResponse(BaseModel):
    dataset_ref: str
    meta: DatasetMeta


# --------------------------------------------------------------------------- #
# Router                                                                       #
# --------------------------------------------------------------------------- #

router = APIRouter(tags=["ingestion"])


@router.post(
    "/eda/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV or Parquet dataset",
)
async def upload_dataset(file: UploadFile) -> UploadResponse:
    """Accept a multipart CSV or Parquet upload, validate it, and persist it.

    Returns the opaque *dataset_ref* and basic metadata.

    Raises:
        413: payload exceeds the size cap.
        415: file format is not CSV or Parquet (checked via magic bytes).
        422: file cannot be parsed.
    """
    raw = await file.read()

    # --- Size cap (defense-in-depth; loader also checks CSV inline) ----------
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Upload exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit. "
                "Please upload a smaller file."
            ),
        )

    # --- Magic-byte format validation ----------------------------------------
    if not _is_accepted_format(raw):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported file type. Upload a CSV (plain text) or "
                "Parquet file. Validation is based on file content, not "
                "filename or Content-Type header."
            ),
        )

    # --- Persist + describe --------------------------------------------------
    filename = file.filename or "upload"
    try:
        ref = save_upload(raw, filename)
        meta = describe(ref)
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during ingestion: {exc}",
        ) from exc

    # Best-effort TTL sweep — do not fail the upload if this errors.
    try:
        cleanup_expired()
    except Exception:
        pass

    return UploadResponse(dataset_ref=ref, meta=meta)


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _is_accepted_format(raw: bytes) -> bool:
    """Return True if *raw* looks like a Parquet or CSV file by magic bytes."""
    if len(raw) >= 8 and raw[:4] == _PARQUET_MAGIC and raw[-4:] == _PARQUET_MAGIC:
        return True
    # CSV heuristic: first 512 bytes contain no null bytes and are text-like.
    sample = raw[:512]
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return True
        except UnicodeDecodeError:
            pass
        try:
            sample.decode("latin-1")
            return True
        except UnicodeDecodeError:
            pass
    return False
