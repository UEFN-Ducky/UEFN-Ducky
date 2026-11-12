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

    from backend.uefn_plugins.plugin_host import backends, lifecycle, loader, state
    from backend.uefn_plugins.plugin_host.testing import patch_host

    pid, name = "fakec", loader.backend_module_name("fakec")
    manifest = {"label": "Fake Compiled"}
    monkeypatch.setattr(importlib.machinery, "ExtensionFileLoader", _CountingLoader)
    monkeypatch.setattr(_CountingLoader, "runs", 0)
    patch_host(monkeypatch, "_COMPILED_LOADED", {})
    patch_host(monkeypatch, "_RESTART_PENDING", {})
    patch_host(monkeypatch, "_restore_compiled_from_quarantine", lambda *_a: None)
    patch_host(monkeypatch, "is_plugin_enabled", lambda _pid: True)
    monkeypatch.delitem(sys.modules, name, raising=False)
    old = tmp_path / f"{name}.pyd"
    old.write_bytes(b"v1")

    first = backends._import_compiled_backend(pid, tmp_path, manifest)
    assert _CountingLoader.runs == 1

    # The reload's cleanup keeps the compiled module; a pure-Python backend is still dropped.
    pure = loader.backend_module_name("purepy")
    monkeypatch.setitem(sys.modules, pure, types.ModuleType(pure))
    lifecycle._drop_plugin_runtime(pid, name)
    lifecycle._drop_plugin_runtime("purepy", pure)
    assert sys.modules.get(name) is first and pure not in sys.modules

    # Same file again: the running module, no init, nothing to restart for.
    assert backends._import_compiled_backend(pid, tmp_path, manifest) is first
    assert _CountingLoader.runs == 1 and state._RESTART_PENDING == {}

    # A newer build: still the running module, and the plugin says to restart.
    new = tmp_path / f"{name}_v2.pyd"
    new.write_bytes(b"v2")
    later = old.stat().st_mtime + 10
    os.utime(new, (later, later))
    sys.modules.pop(name, None)  # even if something else dropped it
    assert backends._import_compiled_backend(pid, tmp_path, manifest) is first
    assert _CountingLoader.runs == 1
    assert state._RESTART_PENDING == {pid: "Restart UEFN Ducky to finish updating Fake Compiled"}
    sys.modules.pop(name, None)


def test_a_new_version_loads_next_to_the_running_one_without_restart(monkeypatch, tmp_path) -> None:
    # Nov 12 2026: every Discord update said "Restart UEFN Ducky to finish updating" and the app
    # kept running the old code. A build named per version loads fresh next to the old one.
    import importlib.machinery

    from backend.uefn_plugins.plugin_host import backends, lifecycle, loader, state
    from backend.uefn_plugins.plugin_host.testing import patch_host

    pid = "fakev"
    base = loader.backend_module_name(pid)
    v1, v2 = loader.compiled_module_name(pid, 1001000), loader.compiled_module_name(pid, 1002000)
    assert (v1, v2) == ("uefn_plugin_fakev_v1001000", "uefn_plugin_fakev_v1002000")
    monkeypatch.setattr(importlib.machinery, "ExtensionFileLoader", _CountingLoader)
    monkeypatch.setattr(_CountingLoader, "runs", 0)
    patch_host(monkeypatch, "_COMPILED_LOADED", {})
    patch_host(monkeypatch, "_RESTART_PENDING", {})
    patch_host(monkeypatch, "_restore_compiled_from_quarantine", lambda *_a: None)
    patch_host(monkeypatch, "is_plugin_enabled", lambda _pid: True)
    for n in (base, v1, v2):
        monkeypatch.delitem(sys.modules, n, raising=False)

    (tmp_path / f"{v1}.pyd").write_bytes(b"v1")
    m1 = {"label": "Fake V", "backend": {"compiled": f"{v1}.pyd", "module": v1}}
    first = backends._import_compiled_backend(pid, tmp_path, m1)
    assert _CountingLoader.runs == 1 and sys.modules[base] is first

    # The Store update: new file, new name -> its own init runs, nothing waits for a restart.
    lifecycle._drop_plugin_runtime(pid, base)
    assert base not in sys.modules and sys.modules[v1] is first  # the running copy stays mapped
    (tmp_path / f"{v1}.pyd").unlink()
    (tmp_path / f"{v2}.pyd").write_bytes(b"v2")
    m2 = {"label": "Fake V", "backend": {"compiled": f"{v2}.pyd", "module": v2}}
    second = backends._import_compiled_backend(pid, tmp_path, m2)
    assert second is not first and _CountingLoader.runs == 2
    assert sys.modules[base] is second and state._RESTART_PENDING == {}

    # Enabling again without an update reuses the running copy (no second init).
    assert backends._import_compiled_backend(pid, tmp_path, m2) is second
    assert _CountingLoader.runs == 2
    for n in (base, v1, v2):
        sys.modules.pop(n, None)
