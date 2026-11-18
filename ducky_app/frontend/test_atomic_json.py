"""Atomic JSON: short temp names so Windows MAX_PATH does not abort the write."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from frontend import atomic_json
from frontend.atomic_json import write_json_atomic, write_json_plain, write_text_atomic


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


def test_plain_writer_makes_no_backup(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "state.json"
    write_json_plain(target, {"port": 4210}, indent=1)
    write_json_plain(target, {"port": 4211}, indent=1)
    assert json.loads(target.read_text(encoding="utf-8")) == {"port": 4211}
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_eight_threads_writing_one_file_never_fail(tmp_path: Path) -> None:
    target = tmp_path / "d" / "shared.json"
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                write_json_plain(target, {"thread": n, "i": i})
                json.loads(target.read_text(encoding="utf-8"))
        except BaseException as exc:  # noqa: BLE001 - collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert set(json.loads(target.read_text(encoding="utf-8"))) == {"thread", "i"}
    assert [p.name for p in target.parent.iterdir()] == ["shared.json"]


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "d" / "state.json"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(atomic_json.os, "replace", boom)
    monkeypatch.setattr(atomic_json.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_dumps_kwargs_are_passed(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    write_json_plain(target, {"b": 1, "a": 2}, indent=1, sort_keys=True)
    assert target.read_bytes() == b'{\n "a": 2,\n "b": 1\n}'
