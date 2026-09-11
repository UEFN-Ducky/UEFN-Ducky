"""Model catalog disk cache survives restarts (fast first chat open)."""

from __future__ import annotations

import json

import threading
import time
from unittest.mock import patch

import frontend.ui_web.panel_api as pa
from backend.agent.model_fetch import _CAPABILITY_CACHE, ModelInfo, get_model_info


def test_model_cache_disk_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["anthropic"] = [
        ModelInfo(id="claude-x", display_name="Claude X", supports_vision=True, price_in=3.0)
    ]
    monkeypatch.setattr(pa, "_contributed_provider_ids", lambda: set())

    with patch("backend.agent.providers.all_providers", return_value=("anthropic",)):
        pa._save_model_cache_to_disk()
        pa._model_cache.clear()
        _CAPABILITY_CACHE.clear()
        pa._load_model_cache_from_disk()

    m = pa._model_cache["anthropic"][0]
    assert m.id == "claude-x" and m.supports_vision and m.price_in == 3.0
    # Capability lookups (vision/tools/pricing) must also work from the disk load.
    assert get_model_info("anthropic", "claude-x") is not None


def test_load_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._load_model_cache_from_disk()
    assert pa._model_cache == {}


def test_prune_drops_models_for_removed_gateway(tmp_path, monkeypatch):
    """Keep-data uninstall leaves the API key; catalog must still drop that gateway."""
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["openai"] = [ModelInfo(id="o4-mini", display_name="o4-mini")]
    pa._model_cache["anthropic"] = [ModelInfo(id="claude-x", display_name="Claude X")]
    monkeypatch.setattr(pa, "_contributed_provider_ids", lambda: set())

    with patch("backend.agent.providers.all_providers", return_value=("anthropic",)):
        pa._prune_model_caches_to_enabled_providers()

    assert "openai" not in pa._model_cache
    assert "anthropic" in pa._model_cache
    # The persisted copy (ducky.db cache_docs) must not keep the removed gateway either.
    from backend.store.repos import kv

    raw = json.dumps(kv.get_doc("cache_docs", "models_cache"))
    assert "openai" not in raw
    assert "anthropic" in raw


def test_get_models_returns_cache_without_fetch(monkeypatch):
    """pywebview get_models must not call provider APIs on the caller thread."""
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin

    monkeypatch.setattr(pa, "_model_cache", {
        "anthropic": [ModelInfo(id="claude-x", display_name="Claude X", supports_tools=True)],
    })
    kicks: list[str] = []
    monkeypatch.setattr(pa, "kick_model_refresh", lambda: kicks.append("kick"))

    def _boom(*_a, **_k):
        raise AssertionError("fetch_models ran on the RPC thread")

    monkeypatch.setattr("backend.agent.model_fetch.fetch_models", _boom)
    rows = PanelApiSettingsMixin().get_models("anthropic", False)
    assert rows[0]["id"] == "claude-x"
    assert rows[0]["provider_key"] == "anthropic"
    assert kicks == []


def test_get_models_refresh_kicks_background_and_returns_cache(monkeypatch):
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin

    monkeypatch.setattr(pa, "_model_cache", {
        "openai": [ModelInfo(id="gpt-4o", display_name="GPT-4o")],
    })
    kicks: list[str] = []
    monkeypatch.setattr(pa, "kick_model_refresh", lambda: kicks.append("kick"))

    def _boom(*_a, **_k):
        raise AssertionError("fetch_models ran on the RPC thread")

    monkeypatch.setattr("backend.agent.model_fetch.fetch_models", _boom)
    catalog = PanelApiSettingsMixin().get_models_catalog(True)
    assert kicks == ["kick"]
    assert catalog["models"][0]["id"] == "gpt-4o"


def test_prune_keeps_cache_when_factory_not_registered(tmp_path, monkeypatch):
    """Boot can be 'ready' before register() — do not wipe Anthropic off disk."""
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["anthropic"] = [ModelInfo(id="claude-x", display_name="Claude X")]
    monkeypatch.setattr(pa, "_contributed_provider_ids", lambda: {"anthropic"})
    with patch("backend.agent.providers.all_providers", return_value=()):
        pa._prune_model_caches_to_enabled_providers()
    assert "anthropic" in pa._model_cache


def test_prune_empty_keep_does_not_wipe(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["anthropic"] = [ModelInfo(id="claude-x", display_name="Claude X")]
    monkeypatch.setattr(pa, "_contributed_provider_ids", lambda: set())
    with patch("backend.agent.providers.all_providers", return_value=()):
        pa._prune_model_caches_to_enabled_providers()
    assert "anthropic" in pa._model_cache


def test_save_skips_when_no_providers_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["anthropic"] = [ModelInfo(id="claude-x", display_name="Claude X")]
    monkeypatch.setattr(pa, "_contributed_provider_ids", lambda: set())
    with patch("backend.agent.providers.all_providers", return_value=()):
        pa._save_model_cache_to_disk()
    assert not (tmp_path / pa._MODELS_CACHE_FILE).exists()


def test_kick_model_refresh_returns_before_fetch(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _slow_warm() -> None:
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(pa, "_warm_model_cache", _slow_warm)
    with pa._models_refresh_lock:
        pa._models_refresh_inflight = False
    t0 = time.perf_counter()
    pa.kick_model_refresh()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 200.0, f"kick_model_refresh blocked: {elapsed_ms:.0f}ms"
    assert started.wait(timeout=1.0)
    release.set()
