"""A compiled plugin backend runs its init once per process, even when the Store reloads it."""

from __future__ import annotations

import os
import sys
import types


class _CountingLoader:
    """Stands in for ExtensionFileLoader: counts how often the extension's init runs."""

    runs = 0

    def __init__(self, name, path):
        self.name, self.path = name, path

    def create_module(self, spec):
        return None

    def exec_module(self, mod):
        type(self).runs += 1


def test_a_loaded_compiled_backend_is_never_executed_again(monkeypatch, tmp_path) -> None:
    # Oct 29 2026: a Store reinstall re-ran the Blender .pyd's init -> ACCESS_VIOLATION in python313.dll.
    import importlib.machinery

    from backend.uefn_plugins import host

    pid, name = "fakec", host.backend_module_name("fakec")
    manifest = {"label": "Fake Compiled"}
    monkeypatch.setattr(importlib.machinery, "ExtensionFileLoader", _CountingLoader)
    monkeypatch.setattr(_CountingLoader, "runs", 0)
    monkeypatch.setattr(host, "_COMPILED_LOADED", {})
    monkeypatch.setattr(host, "_RESTART_PENDING", {})
    monkeypatch.setattr(host, "_restore_compiled_from_quarantine", lambda *_a: None)
    monkeypatch.setattr(host, "is_plugin_enabled", lambda _pid: True)
    monkeypatch.delitem(sys.modules, name, raising=False)
    old = tmp_path / f"{name}.pyd"
    old.write_bytes(b"v1")

    first = host._import_compiled_backend(pid, tmp_path, manifest)
    assert _CountingLoader.runs == 1

    # The reload's cleanup keeps the compiled module; a pure-Python backend is still dropped.
    pure = host.backend_module_name("purepy")
    monkeypatch.setitem(sys.modules, pure, types.ModuleType(pure))
    host._drop_plugin_runtime(pid, name)
    host._drop_plugin_runtime("purepy", pure)
    assert sys.modules.get(name) is first and pure not in sys.modules

    # Same file again: the running module, no init, nothing to restart for.
    assert host._import_compiled_backend(pid, tmp_path, manifest) is first
    assert _CountingLoader.runs == 1 and host._RESTART_PENDING == {}

    # A newer build: still the running module, and the plugin says to restart.
    new = tmp_path / f"{name}_v2.pyd"
    new.write_bytes(b"v2")
    later = old.stat().st_mtime + 10
    os.utime(new, (later, later))
    sys.modules.pop(name, None)  # even if something else dropped it
    assert host._import_compiled_backend(pid, tmp_path, manifest) is first
    assert _CountingLoader.runs == 1
    assert host._RESTART_PENDING == {pid: "Restart UEFN Ducky to finish updating Fake Compiled"}
    sys.modules.pop(name, None)
