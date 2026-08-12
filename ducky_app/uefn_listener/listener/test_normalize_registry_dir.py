"""Unit tests for ARFilter directory normalization (no Unreal required)."""

from __future__ import annotations

import ast
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "handlers" / "assets.py"
_src = _ASSETS.read_text(encoding="utf-8")
_tree = ast.parse(_src)
_fn = next(
    n
    for n in _tree.body
    if isinstance(n, ast.FunctionDef) and n.name == "_normalize_registry_dir"
)
# Rebuild a pure callable from the AST body (no unreal import).
_ns: dict = {}
exec(compile(ast.Module(body=[_fn], type_ignores=[]), str(_ASSETS), "exec"), _ns)
_normalize_registry_dir = _ns["_normalize_registry_dir"]


def test_strips_trailing_slash():
    assert _normalize_registry_dir("/Game/") == "/Game"
    assert _normalize_registry_dir("/Game/Creative/") == "/Game/Creative"


def test_empty_falls_back_to_game():
    assert _normalize_registry_dir("") == "/Game"
    assert _normalize_registry_dir("   ") == "/Game"
    assert _normalize_registry_dir("/") == "/Game"


def test_already_normalized():
    assert _normalize_registry_dir("/Game") == "/Game"
    assert _normalize_registry_dir("/Game/Creative") == "/Game/Creative"
