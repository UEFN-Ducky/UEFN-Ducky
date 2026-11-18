"""mcp.json export is written atomically: a failed write never leaves half a file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.mcp_plugins import store
from frontend import atomic_json


def test_a_crash_mid_write_keeps_the_old_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "d" / "mcp.json"
    store._write_json(path, {"mcpServers": {"old": {}}})
    assert path.read_bytes() == (json.dumps({"mcpServers": {"old": {}}}, indent=2) + "\n").encode()

    def boom(src, dst):
        raise OSError("killed mid-write")

    monkeypatch.setattr(atomic_json.os, "replace", boom)
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _s: None)
    with pytest.raises(OSError):
        store._write_json(path, {"mcpServers": {"new": {}}})
    assert store._read_json(path) == {"mcpServers": {"old": {}}}
    assert sorted(p.name for p in path.parent.iterdir()) == ["mcp.json"]
    assert store._read_json(tmp_path / "missing.json") is None
