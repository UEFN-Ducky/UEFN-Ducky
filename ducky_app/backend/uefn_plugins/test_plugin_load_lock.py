"""Two loads of one plugin never run at once (Oct 29 2026: a Store reload and the repair worker
both loaded Blender; its compiled module's init crashed in python313.dll)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from backend.uefn_plugins import host


def test_loads_of_the_same_plugin_wait_for_each_other_and_others_do_not(monkeypatch) -> None:
    running: dict[str, int] = {}
    most: dict[str, int] = {}
    guard = threading.Lock()

    def slow_load(pid, root, manifest, register=True):
        with guard:
            running[pid] = running.get(pid, 0) + 1
            most[pid] = max(most.get(pid, 0), running[pid])
        time.sleep(0.2)
        with guard:
            running[pid] -= 1

    monkeypatch.setattr(host, "_load_one", slow_load)
    threads = [threading.Thread(target=host._load_enabled_plugin, args=(pid, Path("."), {}))
               for pid in ("blender", "blender", "blender", "forge")]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert most == {"blender": 1, "forge": 1}
    assert time.monotonic() - started >= 0.6  # the three Blender loads ran one after another


def test_a_load_already_running_makes_a_second_wait_not_skip(monkeypatch) -> None:
    order: list[str] = []
    lock = host._plugin_load_lock("piper")
    monkeypatch.setattr(host, "_load_one", lambda *a, **k: order.append("load"))
    with lock:
        t = threading.Thread(target=host._load_enabled_plugin, args=("piper", Path("."), {}))
        t.start()
        time.sleep(0.1)
        order.append("first done")
    t.join(5)
    assert order == ["first done", "load"]
