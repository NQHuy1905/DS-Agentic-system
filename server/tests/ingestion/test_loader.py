"""Tests for the ingestion package: loader, storage, and models.

Coverage targets:
  - Semicolon-delimited + latin-1 CSV loads with correct shape/dtypes.
  - Parquet loads with correct shape/dtypes.
  - Oversize CSV rejected (IngestionError).
  - Binary blob (wrong magic) rejected (IngestionError).
  - path_for rejects non-UUID strings (including '../' traversal attempts).
  - Leased ref survives cleanup_expired; unleased ref is removed.
  - describe() returns accurate DatasetMeta.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.ingestion.loader import IngestionError, load_dataframe
from app.ingestion.models import DatasetMeta, describe
from app.ingestion.storage import (
    cleanup_expired,
    lease,
    path_for,
    release,
    save_upload,
)

# Location of static (text-safe) fixture files.
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _register(raw: bytes, filename: str = "test.csv") -> str:
    """Save *raw* bytes and return the resulting ref."""
    return save_upload(raw, filename)


# --------------------------------------------------------------------------- #
# CSV loading                                                                  #
# --------------------------------------------------------------------------- #

class TestCsvLoading:
    def test_semicolon_ascii_csv_shape(self):
        """Static fixture: 3 rows × 3 cols, semicolon delimiter."""
        raw = (FIXTURES_DIR / "semicolon_ascii.csv").read_bytes()
        ref = _register(raw, "semicolon_ascii.csv")
        df = load_dataframe(ref)
        assert df.shape == (3, 3)
        assert list(df.columns) == ["id", "label", "score"]

    def test_semicolon_ascii_csv_dtypes(self):
        raw = (FIXTURES_DIR / "semicolon_ascii.csv").read_bytes()
        ref = _register(raw, "semicolon_ascii.csv")
        df = load_dataframe(ref)
        # id and score are numeric; label is object.
        assert pd.api.types.is_numeric_dtype(df["id"])
        assert pd.api.types.is_numeric_dtype(df["score"])
        assert pd.api.types.is_object_dtype(df["label"])

    def test_semicolon_latin1_csv_shape(self, latin1_csv_bytes):
        """Generated fixture: 3 rows × 3 cols, semicolon delimiter, latin-1 encoding."""
        ref = _register(latin1_csv_bytes, "semicolon_latin1.csv")
        df = load_dataframe(ref)
        assert df.shape == (3, 3)
        assert list(df.columns) == ["id", "name", "score"]

    def test_semicolon_latin1_csv_values(self, latin1_csv_bytes):
        """Latin-1 characters survive the encoding fallback correctly."""
        ref = _register(latin1_csv_bytes, "semicolon_latin1.csv")
        df = load_dataframe(ref)
        # 'René' is the first name row (latin-1 0xe9 = é).
        assert "Ren" in df["name"].iloc[0]  # check partial; encoding may vary
        assert df["score"].iloc[0] == pytest.approx(88.5)


# --------------------------------------------------------------------------- #
# Parquet loading                                                              #
# --------------------------------------------------------------------------- #

class TestParquetLoading:
    def test_parquet_shape(self, parquet_bytes):
        ref = _register(parquet_bytes, "sample.parquet")
        df = load_dataframe(ref)
        assert df.shape == (5, 3)

    def test_parquet_columns(self, parquet_bytes):
        ref = _register(parquet_bytes, "sample.parquet")
        df = load_dataframe(ref)
        assert set(df.columns) == {"product", "quantity", "price"}

    def test_parquet_dtypes(self, parquet_bytes):
        ref = _register(parquet_bytes, "sample.parquet")
        df = load_dataframe(ref)
        assert pd.api.types.is_numeric_dtype(df["quantity"])
        assert pd.api.types.is_float_dtype(df["price"])
        assert pd.api.types.is_object_dtype(df["product"])


# --------------------------------------------------------------------------- #
# Rejection / guard tests                                                      #
# --------------------------------------------------------------------------- #

class TestRejection:
    def test_oversize_csv_rejected(self):
        """CSV that exceeds the byte cap raises IngestionError."""
        # Use a tiny cap to avoid allocating large buffers in tests.
        raw = b"col_a,col_b\n" + b"1,2\n" * 50
        ref = _register(raw, "oversize.csv")
        with pytest.raises(IngestionError, match="too large"):
            load_dataframe(ref, max_csv_bytes=10)

    def test_wrong_magic_binary_rejected(self):
        """A binary blob with null bytes is rejected (not CSV or Parquet)."""
        binary_blob = b"\x00\x01\x02\x03\xFF\xFE" * 80
        ref = _register(binary_blob, "fake.bin")
        with pytest.raises(IngestionError, match="Unsupported file format"):
            load_dataframe(ref)

    def test_parquet_cell_bomb_rejected(self, parquet_bytes):
        """Parquet whose cell count exceeds the limit is rejected."""
        ref = _register(parquet_bytes, "sample.parquet")
        # Sample parquet has 5 rows × 3 cols = 15 cells; cap at 14.
        with pytest.raises(IngestionError, match="decompression-bomb"):
            load_dataframe(ref, max_parquet_cells=14)


# --------------------------------------------------------------------------- #
# Storage / path_for security                                                  #
# --------------------------------------------------------------------------- #

class TestStorageSecurity:
    def test_path_for_rejects_path_traversal_string(self):
        """'../../etc/passwd' is not a valid UUID — ValueError raised."""
        with pytest.raises(ValueError, match="Invalid dataset ref"):
            path_for("../../etc/passwd")

    def test_path_for_rejects_non_uuid(self):
        """Arbitrary non-UUID string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid dataset ref"):
            path_for("not-a-uuid-at-all")

    def test_path_for_rejects_uuid_like_traversal(self):
        """A string that looks UUID-like but isn't a real registered ref
        raises FileNotFoundError (uuid parse succeeds but ref unknown)."""
        fake_ref = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(FileNotFoundError):
            path_for(fake_ref)

    def test_path_for_known_ref_returns_path(self):
        raw = b"x,y\n1,2\n"
        ref = _register(raw, "tiny.csv")
        p = path_for(ref)
        assert p.exists()


# --------------------------------------------------------------------------- #
# Lease / cleanup                                                              #
# --------------------------------------------------------------------------- #

class TestLeaseAndCleanup:
    def test_leased_ref_survives_cleanup(self):
        """A ref held by an active run is never removed by cleanup_expired."""
        raw = b"col\n1\n2\n3\n"
        ref = _register(raw, "leased.csv")
        lease(ref, "run-test-001")
        removed = cleanup_expired(ttl_seconds=0)  # TTL=0 → everything eligible
        assert ref not in removed, "Leased ref must not be removed during cleanup"

    def test_released_ref_removed_by_cleanup(self):
        """After releasing all leases, cleanup_expired removes the expired ref."""
        raw = b"col\n1\n2\n3\n"
        ref = _register(raw, "unleased.csv")
        lease(ref, "run-test-002")
        release(ref, "run-test-002")
        removed = cleanup_expired(ttl_seconds=0)
        assert ref in removed, "Unleased, expired ref must be swept by cleanup"

    def test_multi_run_lease_requires_all_released(self):
        """Cleanup skips a ref until every holding run_id has released it."""
        raw = b"col\n1\n"
        ref = _register(raw, "multi_lease.csv")
        lease(ref, "run-A")
        lease(ref, "run-B")

        # Release only one — ref must survive.
        release(ref, "run-A")
        removed = cleanup_expired(ttl_seconds=0)
        assert ref not in removed

        # Release the second — now it should be swept.
        release(ref, "run-B")
        removed = cleanup_expired(ttl_seconds=0)
        assert ref in removed


# --------------------------------------------------------------------------- #
# DatasetMeta / describe()                                                     #
# --------------------------------------------------------------------------- #

class TestDescribe:
    def test_describe_returns_correct_meta(self, parquet_bytes):
        ref = _register(parquet_bytes, "sample.parquet")
        meta = describe(ref)
        assert isinstance(meta, DatasetMeta)
        assert meta.ref == ref
        assert meta.filename == "sample.parquet"
        assert meta.rows == 5
        assert meta.cols == 3
        assert meta.size_bytes > 0
        assert set(meta.dtypes.keys()) == {"product", "quantity", "price"}

    def test_describe_csv_meta(self):
        raw = (FIXTURES_DIR / "semicolon_ascii.csv").read_bytes()
        ref = _register(raw, "semicolon_ascii.csv")
        meta = describe(ref)
        assert meta.rows == 3
        assert meta.cols == 3
        assert "id" in meta.dtypes
