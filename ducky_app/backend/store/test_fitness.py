"""Architecture fitness (ADR 0003): only ``backend/store`` touches SQLite.

Mirrors ``backend/workspace/test_no_direct_writes.py``: scan the tree, fail on
a violation, keep the allowlist honest.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]  # ducky_app/
STORE_DIR = "backend/store"

# path -> reason. Empty on purpose; add only with a reason a reviewer accepts.
ALLOWLIST: dict[str, str] = {}


def _imports_sqlite(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] in {"sqlite3", "_sqlite3", "aiosqlite"} for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in {"sqlite3", "_sqlite3", "aiosqlite"}:
                return True
    return False


def _offenders() -> list[str]:
    out: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).as_posix()
        if rel.startswith(STORE_DIR + "/") or "/node_modules/" in rel or "/.venv/" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        if _imports_sqlite(tree) and rel not in ALLOWLIST:
            out.append(rel)
    return out


def test_only_the_store_package_imports_sqlite() -> None:
    bad = _offenders()
    assert not bad, (
        "sqlite3 imported outside backend/store/. Go through a repo in backend/store/repos "
        "or add to ALLOWLIST with a reason:\n" + "\n".join(f"  {p}" for p in bad)
    )


def test_allowlist_has_no_stale_entries() -> None:
    for rel in ALLOWLIST:
        assert (APP_ROOT / rel).is_file(), f"stale allowlist entry: {rel}"


def test_listener_never_imports_the_store() -> None:
    """The in-editor listener runs on Epic's Python and must never open ducky.db."""
    for path in sorted((APP_ROOT / "uefn_listener").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "backend.store" not in src and "sqlite3" not in src, path
