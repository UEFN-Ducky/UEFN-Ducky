"""Unloading a plugin: it waits for a load in flight, and drops what that load registered."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from backend.uefn_plugins.plugin_host import lifecycle, loader, state


def _write_plugin(root: Path, pid: str, backend_src: str) -> None:
    plugin = root / pid
    (plugin / "backend").mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"id": pid, "kind": "plugin", "version": 1, "label": pid}),
        encoding="utf-8",
    )
    (plugin / "backend" / "__init__.py").write_text(backend_src, encoding="utf-8")


def test_unloading_a_plugin_waits_for_its_load_to_finish() -> None:
    """Oct 11 2026: a reload dropped the plugin's modules while another thread was still
    importing it, and that load failed with KeyError 'uefn_plugin_island_media_kit'."""
    src = (
        "import time\n"
        "def register(api):\n"
        "    time.sleep(0.4)\n"
        "    from . import late\n"
        "    late.ok()\n"
    )
    errors: list[BaseException] = []
    registered: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_plugin(root, "racy", src)
        (root / "racy" / "backend" / "late.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        manifest = json.loads((root / "racy" / "plugin.json").read_text(encoding="utf-8"))
        manifest["backend"] = {"entry": "backend", "register": "register"}

        def load() -> None:
            try:
                loader._load_one("racy", root / "racy", manifest)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with (
            patch.object(loader, "_REGISTERED", registered),
            patch.object(lifecycle, "_REGISTERED", registered),
            patch.dict(state._LOADED_SIGNATURE),
        ):
            sys.modules.pop("uefn_plugin_racy", None)
            worker = threading.Thread(target=load)
            worker.start()
            time.sleep(0.15)
            lifecycle.invalidate_plugin_runtime("racy")
            worker.join(timeout=10)
            assert errors == []
            assert "racy" not in registered  # the unload came after the load
            assert not [k for k in sys.modules if k.startswith("uefn_plugin_racy")]


def test_wait_plugin_toggles_joins_a_running_toggle() -> None:
    done = threading.Event()
    lifecycle._spawn_toggle_thread(lambda: (time.sleep(0.2), done.set()), name="test-toggle")
    lifecycle.wait_plugin_toggles(timeout=5.0)
    assert done.is_set()


def test_manifest_signature_is_zero_without_a_manifest(tmp_path) -> None:
    assert lifecycle._manifest_signature(tmp_path) == 0
    (tmp_path / "plugin.json").write_text("{}", encoding="utf-8")
    assert lifecycle._manifest_signature(tmp_path) > 0
