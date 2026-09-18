"""Atomic JSON: short temp names so Windows MAX_PATH does not abort the write."""

from __future__ import annotations

import json
from pathlib import Path

from frontend.atomic_json import write_json_atomic


def test_write_json_atomic_survives_long_nested_path(tmp_path: Path) -> None:
    deep = tmp_path
    for i in range(8):
        deep = deep / f"segment_name_{i:02d}_padded"
    target = (
        deep
        / "conversations"
        / "a1f959e6-93e1-4987-9d8e-002e42b6a363"
        / "conversation.json"
    )
    write_json_atomic(target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8"))["ok"] is True
