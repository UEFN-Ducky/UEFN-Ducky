"""The real-AppData guard ignores the live app's own writes, never a test's leak."""
from __future__ import annotations

from pathlib import Path

import conftest


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def test_live_panel_release_locks_and_bytecode_are_not_leaks(tmp_path):
    for rel in ("ai_plugins/.releases/kimi.json",
                "uefn_plugins/ollama/backend/__pycache__/graph_nodes.cpython-313.pyc",
                "ai_plugins/fixture/plugin.json",
                "uefn_plugins/ollama/backend/graph_nodes.py"):
        _touch(tmp_path, rel)
    live = set(conftest._tree_signature(tmp_path, live_panel=True))
    assert {str(Path(p)) for p in ("ai_plugins/fixture/plugin.json",
                                   "uefn_plugins/ollama/backend/graph_nodes.py")} == live
    # Without the panel open, nothing else writes there: everything is watched.
    assert len(conftest._tree_signature(tmp_path, live_panel=False)) == 4
