"""Shared fixtures for the ingestion test suite.

Binary fixtures (latin-1 CSV, Parquet) are generated programmatically so
we never need to commit binary blobs to the repository.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

# Static fixtures directory (tracked in git — plain text only).
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def latin1_csv_bytes() -> bytes:
    """Semicolon-delimited CSV encoded in latin-1 with non-ASCII characters.

    The string 'é' encodes to 0xe9 in latin-1, which is NOT valid UTF-8,
    so this fixture exercises the UTF-8 → latin-1 fallback path.
    """
    text = "id;name;score\n1;Ren\xe9;88.5\n2;caf\xe9;91.0\n3;na\xefve;77.3\n"
    # text is already str with latin-1 codepoints; encode to raw bytes.
    return text.encode("latin-1")


@pytest.fixture(scope="session")
def latin1_csv_path(tmp_path_factory, latin1_csv_bytes) -> Path:
    """Write the latin-1 CSV bytes to a session-scoped temp file."""
    d = tmp_path_factory.mktemp("ingestion_fixtures")
    p = d / "semicolon_latin1.csv"
    p.write_bytes(latin1_csv_bytes)
    return p


@pytest.fixture(scope="session")
def parquet_bytes() -> bytes:
    """Minimal Parquet file with known shape (5 rows × 3 cols)."""
    df = pd.DataFrame(
        {
            "product": ["apple", "banana", "cherry", "date", "elderberry"],
            "quantity": [10, 25, 5, 40, 8],
            "price": [1.2, 0.5, 3.0, 2.1, 4.5],
        }
    )
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


@pytest.fixture(scope="session")
def parquet_path(tmp_path_factory, parquet_bytes) -> Path:
    """Write the Parquet bytes to a session-scoped temp file."""
    d = tmp_path_factory.mktemp("ingestion_fixtures")
    p = d / "sample.parquet"
    p.write_bytes(parquet_bytes)
    return p
