"""Store enable/disable/install/uninstall must not wedge on hung unload / boot load."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _zip_plugin(plugin_id: str = "wedge", version: int = 1) -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": version,
        "label": "Wedge",
        "description": "sync wedge test",
        "default_enabled": False,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
    return buf.getvalue()


def test_invalidate_plugin_runtime_bounds_hanging_unload() -> None:
    import backend.uefn_plugins.host as host

    mod = ModuleType("uefn_plugin_hangunload")
    started = threading.Event()

    def _hang() -> None:
        started.set()
        time.sleep(60)

    mod.unload = _hang  # type: ignore[attr-defined]
    sys.modules["uefn_plugin_hangunload"] = mod
    try:
        t0 = time.perf_counter()
        host.invalidate_plugin_runtime("hangunload", unload_timeout=0.25)
        elapsed = time.perf_counter() - t0
        assert started.wait(1.0), "unload() never started"
        assert elapsed < 2.0, f"invalidate blocked on unload: {elapsed:.2f}s"
    finally:
        sys.modules.pop("uefn_plugin_hangunload", None)


def test_ensure_plugins_loaded_timeout_returns_false() -> None:
    import backend.uefn_plugins.host as host

    with host._LOAD_START_LOCK:
        was_loaded = host._LOADED
        was_thread = host._LOAD_THREAD
        host._LOADED = False
        host._LOAD_DONE.clear()
        host._LOAD_THREAD = threading.Thread(
            target=lambda: time.sleep(60),
            daemon=True,
            name="uefn-plugins-load-fake",
        )
        host._LOAD_THREAD.start()

    try:
        with patch.object(host, "ensure_plugins_loaded_async", lambda **_k: None):
            t0 = time.perf_counter()
            ready = host.ensure_plugins_loaded(timeout=0.2)
            elapsed = time.perf_counter() - t0
        assert ready is False
        assert elapsed < 1.5, f"ensure_plugins_loaded ignored timeout: {elapsed:.2f}s"
    finally:
        with host._LOAD_START_LOCK:
            host._LOADED = was_loaded
            host._LOAD_THREAD = was_thread
            if was_loaded:
                host._LOAD_DONE.set()


def test_uninstall_returns_fast_and_removes_dir() -> None:
    """Disk + settings clear synchronously; hung reload/skills must not stick the bridge."""
    import os

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LOCALAPPDATA"] = tmp
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp

        from backend.uefn_plugins.store import (
            import_plugin_from_bytes,
            is_plugin_installed,
            plugin_dir,
            set_uefn_plugin_enabled,
            uninstall_uefn_plugin,
        )
        import backend.uefn_plugins.host as host

        result = import_plugin_from_bytes(_zip_plugin("wedge", 1), source="local", replace=True)
        assert result.get("ok"), result
        assert set_uefn_plugin_enabled("wedge", True, trust_local=True).get("ok")
        # Let enable bg finish so modules are importable, then uninstall.
        host.wait_plugin_toggles(timeout=5.0)
        dest = plugin_dir("wedge")
        assert dest.is_dir()

        hang = threading.Event()

        def _hanging_reload(plugin_id: str) -> None:
            hang.wait(60)

        with patch.object(host, "reload_single_plugin", _hanging_reload):
            t0 = time.perf_counter()
            out = uninstall_uefn_plugin("wedge", erase_data=False)
            elapsed = time.perf_counter() - t0

        assert out.get("ok"), out
        assert elapsed < 3.0, f"uninstall blocked on teardown: {elapsed:.2f}s"
        assert not is_plugin_installed("wedge")
        assert not dest.exists()
        hang.set()  # release daemon so the process can exit cleanly


def test_reload_single_plugin_bounds_boot_wait() -> None:
    import backend.uefn_plugins.host as host

    calls: list[float] = []

    def _slow_wait(timeout: float = 30.0) -> None:
        calls.append(timeout)
        time.sleep(min(0.01, timeout))

    def _ensure(timeout: float | None = None) -> bool:
        calls.append(-1.0 if timeout is None else float(timeout))
        return False

    with (
        patch.object(host, "wait_plugin_toggles", _slow_wait),
        patch.object(host, "ensure_plugins_loaded", _ensure),
        patch.object(host, "invalidate_plugin_runtime", lambda *_a, **_k: None),
        patch.object(host, "get_enabled_plugin_ids", lambda: []),
    ):
        t0 = time.perf_counter()
        host.reload_single_plugin("missing-plugin-xyz")
        elapsed = time.perf_counter() - t0

    assert calls == [5.0, 10.0], calls
    assert elapsed < 2.0


def _zip_hanging_register(plugin_id: str = "hangreg") -> bytes:
    manifest = {
        "id": plugin_id,
        "kind": "plugin",
        "version": 1,
        "label": "Hang Register",
        "description": "register() blocks forever",
        "default_enabled": False,
        "backend": {"entry": "backend", "register": "register"},
    }
    backend = (
        "import time\n"
        "def register(api):\n"
        "    time.sleep(600)\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", backend)
    return buf.getvalue()


def test_hung_register_does_not_wedge_store_toggles() -> None:
    """ensure_plugins_loaded / disable / invalidate stay fast while register() hangs."""
    import os

    # ignore_cleanup_errors: hung register() keeps the .py module mapped on Windows.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["LOCALAPPDATA"] = tmp
        os.environ["USERPROFILE"] = tmp
        os.environ["HOME"] = tmp

        from backend.uefn_plugins.store import (
            import_plugin_from_bytes,
            set_uefn_plugin_enabled,
        )
        import backend.uefn_plugins.host as host

        # Quiet sibling that must still be unloadable while hangreg is stuck in register().
        other = import_plugin_from_bytes(_zip_plugin("other", 1), source="local", replace=True)
        assert other.get("ok"), other

        hung = import_plugin_from_bytes(_zip_hanging_register("hangreg"), source="local", replace=True)
        assert hung.get("ok"), hung

        # Do not call the real enable path (that would hang a toggle thread on register).
        # Persist enabled=True in settings + mark host as post-boot with hangreg missing.
        from frontend.settings import PanelSettings, replace

        settings = PanelSettings.load()
        settings = replace(
            settings,
            enabled_uefn_plugins=["hangreg"],
            trusted_local_uefn_plugins=["hangreg", "other"],
        )
        settings.save()

        with host._LOCK:
            host._REGISTERED.discard("hangreg")
            host._REGISTERED.discard("other")
        host._LOADED = True
        host._UI_READY = True
        host._LOAD_DONE.set()
        host._REPAIR_ATTEMPTS.clear()
        with host._REPAIR_THREAD_LOCK:
            host._REPAIR_THREAD = None

        t0 = time.perf_counter()
        assert host.ensure_plugins_loaded() is True
        assert time.perf_counter() - t0 < 1.0, "ensure_plugins_loaded blocked on repair"

        # Give the repair daemon a moment to enter the hung register().
        deadline = time.monotonic() + 3.0
        while "hangreg" not in host._REPAIR_ATTEMPTS and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "hangreg" in host._REPAIR_ATTEMPTS

        # Second ensure within backoff must not re-kick hangreg.
        first_attempt = host._REPAIR_ATTEMPTS["hangreg"]
        assert host.ensure_plugins_loaded() is True
        time.sleep(0.05)
        assert host._REPAIR_ATTEMPTS.get("hangreg") == first_attempt

        t1 = time.perf_counter()
        disabled = set_uefn_plugin_enabled("hangreg", False)
        assert disabled.get("ok"), disabled
        assert time.perf_counter() - t1 < 2.0, "disable blocked while register hung"

        t2 = time.perf_counter()
        host.invalidate_plugin_runtime("other", unload_timeout=0.5)
        assert time.perf_counter() - t2 < 2.0, "invalidate blocked while register hung"


def test_reload_single_plugin_register_is_async() -> None:
    """Install-path reload must return before a hung register() finishes."""
    import backend.uefn_plugins.host as host

    started = threading.Event()

    def _hanging_load(pid: str, root: Path, manifest: dict) -> None:
        started.set()
        time.sleep(60)

    fake_root = Path(tempfile.gettempdir()) / "uefn-reloadhang-fake"
    fake_root.mkdir(parents=True, exist_ok=True)
    manifest = {"id": "reloadhang", "kind": "plugin", "version": 1, "label": "Reload Hang"}

    with (
        patch.object(host, "wait_plugin_toggles", lambda timeout=30.0: None),
        patch.object(host, "ensure_plugins_loaded", lambda timeout=None: True),
        patch.object(host, "invalidate_plugin_runtime", lambda *_a, **_k: None),
        patch(
            "backend.uefn_plugins.store.load_plugin_manifest",
            lambda _pid: dict(manifest),
        ),
        patch(
            "backend.uefn_plugins.store.plugin_dir",
            lambda _pid: fake_root,
        ),
        patch.object(host, "get_enabled_plugin_ids", lambda: ["reloadhang"]),
        patch.object(host, "is_plugin_enabled", lambda _pid: True),
        patch.object(host, "_load_enabled_plugin", _hanging_load),
        patch.object(host, "_load_one", lambda *_a, **_k: None),
        patch.object(host, "_notify_uefn_plugins_changed", lambda: None),
        patch.object(host, "_strip_contributions_for", lambda _pid: None),
    ):
        t0 = time.perf_counter()
        host.reload_single_plugin("reloadhang")
        elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"reload_single_plugin blocked on register: {elapsed:.2f}s"
    assert started.wait(2.0), "background register never started"


if __name__ == "__main__":
    test_invalidate_plugin_runtime_bounds_hanging_unload()
    test_ensure_plugins_loaded_timeout_returns_false()
    test_uninstall_returns_fast_and_removes_dir()
    test_reload_single_plugin_bounds_boot_wait()
    test_hung_register_does_not_wedge_store_toggles()
    test_reload_single_plugin_register_is_async()
    print("store_sync self-check OK")
