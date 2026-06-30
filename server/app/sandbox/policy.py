"""AST-level policy checker and restricted builtins builder for the sandbox.

Two responsibilities:
1. check_policy(code) — walk the AST and raise PolicyViolation before any
   execution happens (reject on import, dunder access, string-literal
   paths/URLs, and dangerous attribute chains).
2. make_restricted_builtins() — return a builtins dict with open/__import__
   removed so exec'd code cannot reach the filesystem even if policy is bypassed.

Allowlisted imports: pandas, numpy, math, statistics only.
"""
from __future__ import annotations

import ast
import builtins
from typing import Set

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_IMPORTS: Set[str] = {"pandas", "numpy", "math", "statistics"}

# Top-level attribute names that are stripped from pandas/numpy in the child
# process. Mirrored here so AST can catch direct references too.
BANNED_PANDAS_ATTRS: Set[str] = {
    "read_pickle", "read_csv", "read_parquet", "read_json",
    "read_excel", "read_html", "read_xml", "read_table", "read_fwf",
    "read_clipboard", "read_hdf", "read_feather", "read_orc",
    "read_sas", "read_spss", "read_stata",
    "to_csv", "to_parquet", "to_json", "to_excel",
    "to_html", "to_pickle", "to_hdf", "to_feather",
    "to_orc", "to_stata", "to_gbq",
}

BANNED_NUMPY_ATTRS: Set[str] = {
    "fromfile", "load", "loadtxt", "genfromtxt",
    "save", "savez", "savez_compressed", "savetxt",
}

# URL/path-like prefixes that must not appear as string literals
_PATH_URL_PREFIXES = (
    "/", "\\", "./", "../",
    "http://", "https://", "ftp://", "file://",
    "~", "C:", "D:",
)


class PolicyViolation(ValueError):
    """Raised when submitted code violates the execution policy."""


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _PolicyChecker(ast.NodeVisitor):
    """Walk the AST and raise PolicyViolation on the first violation found."""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_IMPORTS:
                raise PolicyViolation(
                    f"Import '{alias.name}' is not allowed. "
                    f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        top = module.split(".")[0]
        if top not in ALLOWED_IMPORTS:
            raise PolicyViolation(
                f"from-import of '{module}' is not allowed. "
                f"Allowed: {sorted(ALLOWED_IMPORTS)}"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        # Dunder attribute access forbidden
        if attr.startswith("__") and attr.endswith("__"):
            raise PolicyViolation(
                f"Dunder attribute access '.{attr}' is not allowed."
            )
        # Banned pandas reader/writer methods regardless of receiver
        if attr in BANNED_PANDAS_ATTRS:
            raise PolicyViolation(
                f"pandas capability '{attr}' is not allowed in sandbox."
            )
        if attr in BANNED_NUMPY_ATTRS:
            raise PolicyViolation(
                f"numpy capability '{attr}' is not allowed in sandbox."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Catch direct builtins: open(...), __import__(...)
        if isinstance(node.func, ast.Name):
            if node.func.id in ("open", "__import__", "eval", "exec",
                                "compile", "breakpoint"):
                raise PolicyViolation(
                    f"Call to '{node.func.id}' is not allowed."
                )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Reject string literals that look like filesystem paths or URLs.
        if isinstance(node.value, str):
            v = node.value.strip()
            if v and any(v.startswith(p) for p in _PATH_URL_PREFIXES):
                raise PolicyViolation(
                    f"String literal that looks like a path/URL is not allowed: "
                    f"{v[:80]!r}"
                )
        self.generic_visit(node)

    # Python 3.7 AST compat — Str node (superseded by Constant in 3.8+)
    visit_Str = visit_Constant  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_policy(code: str) -> None:
    """Parse and AST-walk *code*; raise PolicyViolation if any rule is broken.

    Call this BEFORE exec. It is fast (pure Python, no execution).
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise PolicyViolation(f"Syntax error in submitted code: {exc}") from exc

    _PolicyChecker().visit(tree)


def make_restricted_builtins() -> dict:
    """Return a copy of builtins with dangerous callables removed.

    This is a defence-in-depth layer; policy.check_policy() is the first gate.
    The exec'd code sees this dict as __builtins__ so even if AST check is
    somehow bypassed, open/__import__/eval etc. are absent.
    """
    safe = vars(builtins).copy()
    blocked = {
        "open", "__import__", "eval", "exec", "compile",
        "breakpoint", "input", "memoryview",
    }
    for name in blocked:
        safe.pop(name, None)

    # Wrap __import__ so only allowlisted modules can be loaded at runtime.
    _orig_import = builtins.__import__

    def _restricted_import(name: str, *args, **kwargs):  # type: ignore[return]
        top = name.split(".")[0]
        if top not in ALLOWED_IMPORTS:
            raise ImportError(
                f"Runtime import of '{name}' blocked by sandbox policy."
            )
        return _orig_import(name, *args, **kwargs)

    safe["__import__"] = _restricted_import
    return safe
