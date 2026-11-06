"""Cursor plugin: disk model cache and an existing SDK install, no npm."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN = (
    Path(__file__).resolve().parents[4]
    / "uefn-plugins"
    / "uefn-plugin-cursor"
    / "backend"
)
# The Cursor plugin is its own repo, beside this checkout; a worktree elsewhere (a sandbox,
# CI) has no copy of it, and every test here failed with FileNotFoundError.
pytestmark = pytest.mark.skipif(not _PLUGIN.is_dir(), reason=f"the Cursor plugin repo isn't at {_PLUGIN}")


def _adapter():
    name = "cursor_plugin_under_test"
    loaded = sys.modules.get(f"{name}.cursor_adapter")
    if loaded is not None:
        return loaded
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(_PLUGIN)]
    pkg.__package__ = name
    sys.modules[name] = pkg
    for mod in ("cursor_effort", "cursor_tool_unwrap", "cursor_adapter"):
        spec = importlib.util.spec_from_file_location(f"{name}.{mod}", _PLUGIN / f"{mod}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = name
        sys.modules[f"{name}.{mod}"] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{name}.cursor_adapter"]


def test_rows_from_http_payload() -> None:
    rows = _adapter()._rows_from_api_payload({"models": ["composer-2.5", "default"]})
    assert [r["id"] for r in rows] == ["composer-2.5", "auto"]
    assert rows[1]["name"] == "Auto"


def test_sandbox_reuses_install_when_npm_missing(tmp_path, monkeypatch) -> None:
    adapter = _adapter()
    app = tmp_path / "app"
    sdk = app / "coding_agents" / "cursor_sdk" / "node_modules" / "@cursor" / "sdk"
    sdk.mkdir(parents=True)
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: app)
    monkeypatch.setattr(adapter, "which_cli", lambda _name, override="": "")
    root, installed = adapter._cursor_sdk_sandbox()
    assert root is not None and installed is False
    assert (root / "node_modules" / "@cursor" / "sdk").is_dir()


def test_sandbox_missing_without_npm_or_install(tmp_path, monkeypatch) -> None:
    adapter = _adapter()
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path / "empty")
    monkeypatch.setattr(adapter, "which_cli", lambda _name, override="": "")
    root, installed = adapter._cursor_sdk_sandbox()
    assert root is None and installed is False


def test_force_refresh_keeps_sdk_when_npm_fails(tmp_path, monkeypatch) -> None:
    adapter = _adapter()
    app = tmp_path / "app"
    sdk = app / "coding_agents" / "cursor_sdk" / "node_modules" / "@cursor" / "sdk"
    sdk.mkdir(parents=True)
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: app)
    monkeypatch.setattr(adapter, "which_cli", lambda _name, override="": "npm")

    class _Proc:
        returncode = 1

    monkeypatch.setattr(adapter.subprocess, "run", lambda *_a, **_k: _Proc())
    root, installed = adapter._cursor_sdk_sandbox(force=True)
    assert root is not None and installed is False


def test_known_models_reads_disk_without_node(tmp_path, monkeypatch) -> None:
    adapter = _adapter()
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("live model fetch")

    monkeypatch.setattr(adapter, "_fetch_live_models", _boom)
    adapter._write_models_cache(
        [{"id": "composer-2.5", "name": "Composer 2.5", "provider": "Cursor"}]
    )
    rows = adapter._known_models("crsr_test")
    assert rows[0]["id"] == "composer-2.5"
    infos = adapter._model_infos_from_rows(rows)
    assert infos[0].id == "composer-2.5"
    assert infos[0].supports_tools is True
