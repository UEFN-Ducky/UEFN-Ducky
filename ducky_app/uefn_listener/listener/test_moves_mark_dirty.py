"""Every agent move of a placed actor marks it dirty, so it is saved (and locked, checked in, undoable).

``set_actor_location`` from Python changes the actor in memory only: without ``modify()``
inside a ``ScopedEditorTransaction`` its package stays clean, a save skips it, and a
shared island never receives it. Found live on Oct 26: a ``set_actor_transform`` move
showed in the viewport but never reached the disk.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MOVES = {"set_actor_location", "set_actor_rotation", "set_actor_scale3d"}
# Functions that move an existing actor on an agent's behalf.
CHECKED = {
    HERE / "handlers" / "actors.py": {"cmd_set_actor_transform"},
    HERE / "registry" / "level_design.py": {
        "_move", "snap_actor_to_grid", "snap_actor_to_ground", "align_actors", "distribute_actors",
    },
}


def _calls(node: ast.AST, names: set[str]) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in names
    ]


def _in_transaction_with_modify(fn: ast.FunctionDef, call: ast.Call) -> bool:
    for w in ast.walk(fn):
        if not isinstance(w, ast.With):
            continue
        if not any("ScopedEditorTransaction" in ast.unparse(item.context_expr) for item in w.items):
            continue
        inner = [n for stmt in w.body for n in ast.walk(stmt)]
        if call in inner and _calls(ast.Module(body=w.body, type_ignores=[]), {"modify"}):
            return True
    return False


@pytest.mark.parametrize("path", sorted(CHECKED), ids=lambda p: p.name)
def test_every_move_sits_in_a_transaction_after_modify(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in CHECKED[path]:
        fn = functions[name]
        for call in _calls(fn, MOVES):
            assert _in_transaction_with_modify(fn, call), f"{path.name}:{call.lineno} {name} moves without modify()"


def test_snaps_move_through_the_helper() -> None:
    tree = ast.parse((HERE / "registry" / "level_design.py").read_text(encoding="utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("snap_actor_to_grid", "snap_actor_to_ground"):
        helper = [
            n for n in ast.walk(functions[name])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_move"
        ]
        assert helper and not _calls(functions[name], MOVES), name
