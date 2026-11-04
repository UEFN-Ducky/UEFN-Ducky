"""Loading one plugin: loads of the same plugin never overlap and register() runs once."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from backend.uefn_plugins.plugin_host import loader


def _write_plugin(root: Path, pid: str, backend_src: str) -> None:
    plugin = root / pid
    (plugin / "backend").mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"id": pid, "kind": "plugin", "version": 1, "label": pid}),
        encoding="utf-8",
    )
    (plugin / "backend" / "__init__.py").write_text(backend_src, encoding="utf-8")


def test_loads_of_the_same_plugin_wait_for_each_other_and_others_do_not(monkeypatch) -> None:
    """Oct 29 2026: a Store reload and the repair worker both loaded Blender; its compiled
    module's init crashed in python313.dll."""
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

    monkeypatch.setattr(loader, "_load_one", slow_load)
    threads = [threading.Thread(target=loader._load_enabled_plugin, args=(pid, Path("."), {}))
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
    lock = loader._plugin_load_lock("piper")
    monkeypatch.setattr(loader, "_load_one", lambda *a, **k: order.append("load"))
    with lock:
        t = threading.Thread(target=loader._load_enabled_plugin, args=("piper", Path("."), {}))
        t.start()
        time.sleep(0.1)
        order.append("first done")
    t.join(5)
    assert order == ["first done", "load"]


def test_two_loaders_of_one_plugin_register_it_once() -> None:
    """The Store's enable worker and the repair pass can load a plugin together.

    A compiled backend initialised twice at once aborts the whole app, so the second
    loader has to wait for the first and then find the plugin registered.
    """
    src = (
        "import time\n"
        "CALLS = []\n"
        "def register(api):\n"
        "    CALLS.append(1)\n"
        "    time.sleep(0.3)\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_plugin(root, "twice", src)
        manifest = json.loads((root / "twice" / "plugin.json").read_text(encoding="utf-8"))
        manifest["backend"] = {"entry": "backend", "register": "register"}
        start = threading.Barrier(2)

        def load() -> None:
            start.wait()
            loader._load_one("twice", root / "twice", manifest)

        with patch.object(loader, "_REGISTERED", set()):
            sys.modules.pop("uefn_plugin_twice", None)
            threads = [threading.Thread(target=load) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert "twice" in loader._REGISTERED
            assert sys.modules["uefn_plugin_twice"].CALLS == [1]
        sys.modules.pop("uefn_plugin_twice", None)


def test_backend_module_name_is_an_identifier() -> None:
    assert loader.backend_module_name("unity-mcp") == "uefn_plugin_unity_mcp"
