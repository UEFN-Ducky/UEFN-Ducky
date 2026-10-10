"""get_editor_log reads the editor's own log, and only its tail — no Unreal required.

Regression: the handler tailed whichever ``*.log`` in the Logs folder was newest.
UEFN's revision-control log (``Lore.log``, 300+ MB after an hour) is written there
too and was newest about half the time, so the Tester never found its
``[DUCKY-TEST]`` lines, and every call ran ``readlines()`` over the whole file
inside the editor.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent / "handlers" / "project.py"

_MB = 1 << 20


@pytest.fixture
def logs(monkeypatch, tmp_path):
    """Load handlers/project.py against a fake ``unreal`` whose log dir is tmp_path."""
    unreal = types.ModuleType("unreal")

    class Paths:
        @staticmethod
        def project_log_dir() -> str:
            return str(tmp_path)

    unreal.Paths = Paths
    pkg = types.ModuleType("listener")
    pkg.__path__ = []
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda _name: (lambda fn: fn)
    save_coalesce = types.ModuleType("listener.save_coalesce")
    save_coalesce.save_now = lambda: True
    serialize = types.ModuleType("listener.serialize")
    serialize.rotator_pyr = lambda r: r
    serialize.serialize = lambda v: v
    for name, mod in (
        ("unreal", unreal),
        ("listener", pkg),
        ("listener.dispatch", dispatch),
        ("listener.save_coalesce", save_coalesce),
        ("listener.serialize", serialize),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("listener_project_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    # Count every byte the handler pulls out of a log file.
    read = {"bytes": 0}

    class Counting:
        def __init__(self, f):
            self._f = f

        def read(self, *args):
            data = self._f.read(*args)
            read["bytes"] += len(data)
            return data

        def readlines(self, *args):
            lines = self._f.readlines(*args)
            read["bytes"] += sum(len(ln) for ln in lines)
            return lines

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

    monkeypatch.setattr(module, "open", lambda *a, **k: Counting(open(*a, **k)), raising=False)
    module._test_dir = tmp_path
    module._test_read = read
    return module


def _write(path: Path, lines: int, *, tail: str = "", mtime: float | None = None) -> Path:
    filler = "".join(f"[2026.10.10-18.00.00:000][  0]LogSlate: filler line {i:07d}\n" for i in range(lines))
    path.write_text(filler + tail, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_the_editor_log_is_read_even_when_another_log_is_newer(logs) -> None:
    now = time.time()
    _write(logs._test_dir / "UnrealEditorFortnite.log", 10,
           tail="LogVerse: [DUCKY-TEST] PASS spawn_works\n", mtime=now - 60)
    # Revision control and CEF write beside it; a rotated copy is newer still.
    _write(logs._test_dir / "Lore.log", 100_000, mtime=now)
    _write(logs._test_dir / "cef3.log", 10, mtime=now)
    _write(logs._test_dir / "UnrealEditorFortnite-backup-2026.10.10-18.50.45.log", 10, mtime=now)

    out = logs.cmd_get_editor_log(last_n=100, regex=r"\[DUCKY-TEST\]")

    assert out["file"].endswith("UnrealEditorFortnite.log")
    assert out["lines"] == ["LogVerse: [DUCKY-TEST] PASS spawn_works"]


def test_a_tail_reads_at_most_one_megabyte_of_a_big_log(logs) -> None:
    path = _write(logs._test_dir / "UnrealEditorFortnite.log", 120_000,
                  tail="LogVerse: [DUCKY-TEST] PASS last\n")
    assert path.stat().st_size > 6 * _MB

    out = logs.cmd_get_editor_log(last_n=100)

    assert logs._test_read["bytes"] <= _MB
    assert out["count"] == 100
    assert out["lines"][-1] == "LogVerse: [DUCKY-TEST] PASS last"
    assert out["lines"][0].startswith("[2026.10.10")  # no half line at the cut
    assert out["offset"] == path.stat().st_size


def test_a_stream_reads_in_bounded_steps_and_loses_no_line(logs) -> None:
    path = _write(logs._test_dir / "UnrealEditorFortnite.log", 10)
    cursor = logs.cmd_get_editor_log(last_n=1)["offset"]
    with path.open("a", encoding="utf-8") as f:
        for i in range(150_000):
            f.write(f"[2026.10.10-18.00.00:000][  0]LogVerse: streamed line {i:07d}\n")
    size = path.stat().st_size
    assert size - cursor > 8 * _MB

    seen: list[str] = []
    calls = 0
    while cursor < size:
        before = logs._test_read["bytes"]
        out = logs.cmd_get_editor_log(since_offset=cursor)
        assert logs._test_read["bytes"] - before <= 4 * _MB
        seen.extend(out["lines"])
        cursor = out["offset"]
        calls += 1
        assert calls < 10

    assert len(seen) == 150_000
    assert seen[0].endswith("LogVerse: streamed line 0000000")
    assert seen[-1].endswith("LogVerse: streamed line 0149999")
