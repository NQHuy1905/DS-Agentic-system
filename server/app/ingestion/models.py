"""Dataset metadata model and describe() helper."""
from __future__ import annotations

from pydantic import BaseModel

from app.ingestion.loader import IngestionError, load_dataframe
from app.ingestion.storage import filename_for


class DatasetMeta(BaseModel):
    ref: str
    filename: str
    rows: int
    cols: int
    size_bytes: int
    dtypes: dict[str, str]  # column_name -> dtype string


def describe(ref: str) -> DatasetMeta:
    """Load the dataset for *ref* and return its metadata.

    Raises:
        IngestionError: if the file cannot be loaded.
        ValueError: if ref is not a valid UUID.
        FileNotFoundError: if ref is unknown.
    """
    path = None
    # We need the actual path to get size_bytes; import here to avoid circular.
    from app.ingestion.storage import path_for

    path = path_for(ref)
    size_bytes = path.stat().st_size
    filename = filename_for(ref)

    df = load_dataframe(ref)
    rows, cols = df.shape
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

    return DatasetMeta(
        ref=ref,
        filename=filename,
        rows=rows,
        cols=cols,
        size_bytes=size_bytes,
        dtypes=dtypes,
    )
