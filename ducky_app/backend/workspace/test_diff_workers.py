from __future__ import annotations

import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor

import pytest

from backend.workspace import diff_workers as dw
from backend.workspace.paths import line_delta as serial_delta


def _slow_comparison(before, after):
    time.sleep(10)
    return 1, 1


def test_pool_is_lazy_and_matches_serial_results():
    workers = dw.DiffWorkers()
    assert workers._pool is None
    try:
        before = "".join(f"Line {n}\n" for n in range(12000))
        pairs = [(before, before.replace(f"Line {n}\n", "Changed\n")) for n in range(6)]
        with ThreadPoolExecutor(max_workers=6) as callers:
            results = list(callers.map(lambda pair: workers.compare(*pair), pairs))
        assert results == [serial_delta(*pair) for pair in pairs]
        children = list(workers._pool._pool)
        assert len(children) <= 2
    finally:
        workers.close()
    assert all(not child.is_alive() for child in children)


def test_service_uses_workers_only_when_enabled_and_large(monkeypatch):
    monkeypatch.setattr(dw, "_workers", None)
    assert dw.line_delta("a\n", "b\n") == (1, 1)
    calls = []

    class Worker:
        def compare(self, before, after):
            calls.append((before, after))
            return serial_delta(before, after)

    monkeypatch.setattr(dw, "_workers", Worker())
    assert dw.line_delta("a\n", "b\n") == (1, 1)
    large = "a\n" * dw._MIN_COMBINED_CHARS
    assert dw.line_delta(large, large) == (0, 0)
    assert not calls
    assert dw.line_delta(large, large + "b\n") == (1, 0)
    assert len(calls) == 1


def test_shutdown_cancels_running_and_waiting_pure_jobs(monkeypatch):
    monkeypatch.setattr(dw, "_local_delta", _slow_comparison)
    workers = dw.DiffWorkers(max_workers=1)
    errors = []

    def compare():
        try:
            workers.compare("a", "b")
        except CancelledError:
            errors.append("cancelled")

    callers = [threading.Thread(target=compare) for _ in range(5)]
    for caller in callers:
        caller.start()
    deadline = time.monotonic() + 5
    while workers._pool is None and time.monotonic() < deadline:
        time.sleep(0.01)
    workers.close()
    for caller in callers:
        caller.join(3)
        assert not caller.is_alive()
    assert errors == ["cancelled"] * 5
    with pytest.raises(CancelledError):
        workers.compare("c", "d")


def test_diff_failure_does_not_modify_the_file(tmp_path, monkeypatch):
    # Use the existing writer's public API with no live project or AppData.
    from backend.workspace import writer as writer_module
    from backend.workspace.writer import ProjectWriter

    root = tmp_path / "island"
    content = root / "Content"
    content.mkdir(parents=True)
    target = content / "test.txt"
    target.write_text("before", encoding="utf-8")
    def cancel_diff(*_):
        raise CancelledError()

    monkeypatch.setattr(writer_module, "line_delta", cancel_diff)
    writer = ProjectWriter.for_root(str(root))
    with pytest.raises(CancelledError):
        writer.write_text("Content/test.txt", "after")
    assert target.read_text(encoding="utf-8") == "before"
