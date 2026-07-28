"""Model catalog disk cache survives restarts (fast first chat open)."""

from __future__ import annotations

from unittest.mock import patch

import frontend.ui_web.panel_api as pa
from backend.agent.model_fetch import _CAPABILITY_CACHE, ModelInfo, get_model_info


def test_model_cache_disk_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pa, "_model_cache", {})
    pa._model_cache["anthropic"] = [
        ModelInfo(id="claude-x", display_name="Claude X", supports_vision=True, price_in=3.0)
    ]

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

    with patch("backend.agent.providers.all_providers", return_value=("anthropic",)):
        pa._prune_model_caches_to_enabled_providers()

    assert "openai" not in pa._model_cache
    assert "anthropic" in pa._model_cache
    # Disk file must not keep the removed gateway either.
    raw = (tmp_path / pa._MODELS_CACHE_FILE).read_text(encoding="utf-8")
    assert "openai" not in raw
    assert "anthropic" in raw
