"""DataFrame loader for ingested datasets.

Supports CSV (UTF-8 + latin-1 fallback, auto-delimiter sniff) and Parquet.
All errors surface as IngestionError so callers get a single typed exception.

Decompression-bomb guards:
  - Parquet: read metadata first; reject if num_rows * num_columns exceeds
    MAX_PARQUET_CELLS (default 50 M cells ~ 400 MB at 8 bytes/cell).
  - CSV: enforce MAX_CSV_BYTES hard limit during streaming read.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Optional

import pandas as pd

from app.ingestion.storage import path_for

# ---------------------------------------------------------------------- #
# Tunable safety limits                                                    #
# ---------------------------------------------------------------------- #
MAX_PARQUET_CELLS: int = 50_000_000   # rows × cols upper bound
MAX_CSV_BYTES: int = 200 * 1024 * 1024  # 200 MB raw-text cap


class IngestionError(Exception):
    """Raised for any file-load failure (bad magic, parse error, size bomb)."""


def load_dataframe(ref: str, max_parquet_cells: Optional[int] = None, max_csv_bytes: Optional[int] = None) -> pd.DataFrame:
    """Load and return a DataFrame for the given *ref*.

    Args:
        ref: Opaque dataset ref returned by storage.save_upload().
        max_parquet_cells: Override for the parquet cell-count bomb limit.
        max_csv_bytes: Override for the CSV streaming byte cap.

    Raises:
        IngestionError: on format errors, size bombs, or parse failures.
        ValueError: propagated from storage.path_for if ref is not a valid UUID.
        FileNotFoundError: propagated from storage.path_for if ref is unknown.
    """
    _max_parquet = max_parquet_cells if max_parquet_cells is not None else MAX_PARQUET_CELLS
    _max_csv = max_csv_bytes if max_csv_bytes is not None else MAX_CSV_BYTES

    path = path_for(ref)
    raw = _read_bytes_guarded(path)
    fmt = _detect_format(raw, path)

    if fmt == "parquet":
        return _load_parquet(raw, _max_parquet)
    elif fmt == "csv":
        return _load_csv(raw, path, _max_csv)
    else:
        raise IngestionError(
            f"Unsupported file format for ref {ref!r}. "
            "Only CSV and Parquet are accepted."
        )


# ---------------------------------------------------------------------- #
# Internal helpers                                                         #
# ---------------------------------------------------------------------- #

_PARQUET_MAGIC = b"PAR1"  # first 4 bytes of every valid parquet file
_CSV_TEXT_THRESHOLD = 512  # bytes to sample for text-detection heuristic


def _read_bytes_guarded(path: Path) -> bytes:
    """Read the file; surface OS errors as IngestionError."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise IngestionError(f"Cannot read file {path.name}: {exc}") from exc


def _detect_format(raw: bytes, path: Path) -> str:
    """Return 'parquet' or 'csv' by inspecting magic bytes, not extension."""
    if raw[:4] == _PARQUET_MAGIC and raw[-4:] == _PARQUET_MAGIC:
        return "parquet"

    # CSV: check the sample is decodable text (no null bytes in first chunk).
    sample = raw[:_CSV_TEXT_THRESHOLD]
    if b"\x00" not in sample:
        return "csv"

    return "unknown"


def _load_parquet(raw: bytes, max_cells: int) -> pd.DataFrame:
    """Read parquet, checking metadata for cell-count bomb before full load."""
    import pyarrow.parquet as pq

    buf = io.BytesIO(raw)
    try:
        pf = pq.ParquetFile(buf)
        meta = pf.metadata
        num_rows = meta.num_rows
        num_cols = meta.num_columns
    except Exception as exc:
        raise IngestionError(f"Cannot read Parquet metadata: {exc}") from exc

    if num_rows * num_cols > max_cells:
        raise IngestionError(
            f"Parquet decompression-bomb guard: {num_rows} rows × {num_cols} cols "
            f"= {num_rows * num_cols:,} cells exceeds limit of {max_cells:,}."
        )

    try:
        buf.seek(0)
        return pd.read_parquet(buf)
    except Exception as exc:
        raise IngestionError(f"Failed to load Parquet file: {exc}") from exc


def _load_csv(raw: bytes, path: Path, max_bytes: int) -> pd.DataFrame:
    """Decode + sniff + parse CSV with a hard byte-cap guard."""
    if len(raw) > max_bytes:
        raise IngestionError(
            f"CSV too large: {len(raw):,} bytes exceeds {max_bytes:,}-byte cap."
        )

    # Encoding: try UTF-8, fall back to latin-1.
    text = _decode_csv(raw, path)

    # Delimiter sniffing via csv.Sniffer on a small sample.
    delimiter = _sniff_delimiter(text)

    try:
        df = pd.read_csv(io.StringIO(text), sep=delimiter, engine="python")
        return df
    except Exception as exc:
        raise IngestionError(f"CSV parse error in {path.name}: {exc}") from exc


def _decode_csv(raw: bytes, path: Path) -> str:
    """Attempt UTF-8 then latin-1 decoding; raise IngestionError on total failure."""
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IngestionError(
        f"Cannot decode {path.name} as UTF-8 or latin-1. "
        "Please re-encode the file and retry."
    )


def _sniff_delimiter(text: str, sample_bytes: int = 4096) -> str:
    """Return the detected delimiter char, defaulting to ',' on any failure."""
    sample = text[:sample_bytes]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","
