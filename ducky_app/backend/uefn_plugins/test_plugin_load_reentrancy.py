"""A plugin register() that re-enters the host must not deadlock the first load.

unity-mcp did exactly that: register() -> set_mcp_server_enabled ->
PanelSettings.save() -> validate() -> contributed_coding_agents() ->
get_contributions() -> ensure_plugins_loaded() -> wait on its own load pass.
_LOADED never flipped, so gateways, models, MCP rows and Store toggles all sat
in "plugins loading" forever.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch


def _write_plugin(root: Path, pid: str, backend_src: str) -> None:
    plugin = root / pid
    (plugin / "backend").mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"id": pid, "kind": "plugin", "version": 1, "label": pid}),
        encoding="utf-8",
    )
    (plugin / "backend" / "__init__.py").write_text(backend_src, encoding="utf-8")


def test_reentrant_register_does_not_deadlock_first_load() -> None:
    import backend.uefn_plugins.host as host

    src = (
        "def register(api):\n"
        "    from backend.uefn_plugins.host import ensure_plugins_loaded, get_contributions\n"
        "    ensure_plugins_loaded()\n"
        "    get_contributions()\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_plugin(root, "reenter", src)
        with (
            patch.object(host, "appdata_uefn_plugins_dir", lambda: root),
            patch.object(host, "get_enabled_plugin_ids", lambda: ["reenter"]),
            patch.object(host, "_LOADED", False),
            patch.object(host, "_UI_READY", False),
            patch.object(host, "_LOAD_DONE", threading.Event()),
            patch.object(host, "_LOAD_THREAD", None),
            patch.object(host, "_REGISTER_TIMEOUT_SEC", 5.0),
        ):
            assert host.ensure_plugins_loaded(timeout=20.0) is True
            assert host.plugins_ready() is True


def test_ensure_plugins_loaded_returns_immediately_inside_load_thread() -> None:
    import backend.uefn_plugins.host as host

    seen: list[bool] = []

    def _worker() -> None:
        host._LOAD_THREAD_STATE.in_load = True
        try:
            seen.append(host.in_plugin_load_thread())
            # Would block forever on _LOAD_DONE without the re-entrancy guard.
            seen.append(host.ensure_plugins_loaded())
        finally:
            host._LOAD_THREAD_STATE.in_load = False

    with (
        patch.object(host, "_LOADED", False),
        patch.object(host, "_LOAD_DONE", threading.Event()),
    ):
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=5.0)

    assert not thread.is_alive(), "ensure_plugins_loaded blocked inside the load thread"
    assert seen == [True, False]


def test_two_loaders_of_one_plugin_register_it_once() -> None:
    """The Store's enable worker and the repair pass can load a plugin together.

    A compiled backend initialised twice at once aborts the whole app, so the second
    loader has to wait for the first and then find the plugin registered.
    """
    import sys

    import backend.uefn_plugins.host as host

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
            host._load_one("twice", root / "twice", manifest)

        with patch.object(host, "_REGISTERED", set()):
            sys.modules.pop("uefn_plugin_twice", None)
            threads = [threading.Thread(target=load) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            assert "twice" in host._REGISTERED
            assert sys.modules["uefn_plugin_twice"].CALLS == [1]
        sys.modules.pop("uefn_plugin_twice", None)
