"""First-run Anthropic / Cursor / OpenAI Store seed — once, never for existing installs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.starter_llm_gateways import (
    STARTER_LLM_GATEWAY_SLUGS,
    ensure_starter_llm_gateways,
    starter_llm_onboard_pending,
)


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


def _write_plugin(root: Path, plugin_id: str) -> None:
    folder = root / "UEFN-Ducky" / "uefn_plugins" / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "kind": "plugin",
                "version": "1.0.0",
                "label": plugin_id,
                "default_enabled": True,
            }
        ),
        encoding="utf-8",
    )


def test_existing_plugin_install_is_grandfathered(isolated_appdata, monkeypatch) -> None:
    _write_plugin(isolated_appdata, "verse")
    calls: list[str] = []

    def _boom(slug: str, **_kwargs):
        calls.append(slug)
        raise AssertionError("must not download for existing installs")

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _boom)
    peek = starter_llm_onboard_pending()
    assert peek["pending"] is False
    assert peek.get("grandfathered") is True
    out = ensure_starter_llm_gateways()
    assert out["first_run"] is False
    assert calls == []


def test_first_run_downloads_three_gateways_once(isolated_appdata, monkeypatch) -> None:
    downloaded: list[str] = []

    def _fake_download(slug: str, **_kwargs):
        downloaded.append(slug)
        _write_plugin(isolated_appdata, slug)
        return {"ok": True, "kind": "plugin", "id": slug}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _fake_download)
    peek = starter_llm_onboard_pending()
    assert peek["pending"] is True
    first = ensure_starter_llm_gateways()
    assert first["first_run"] is True
    assert first["installed"] == list(STARTER_LLM_GATEWAY_SLUGS)
    assert first["errors"] == []
    second = ensure_starter_llm_gateways()
    assert second["first_run"] is False
    assert downloaded == list(STARTER_LLM_GATEWAY_SLUGS)


def test_already_present_gateway_is_skipped(isolated_appdata, monkeypatch) -> None:
    _write_plugin(isolated_appdata, "anthropic")
    downloaded: list[str] = []

    def _fake_download(slug: str, **_kwargs):
        downloaded.append(slug)
        _write_plugin(isolated_appdata, slug)
        return {"ok": True, "kind": "plugin", "id": slug}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _fake_download)
    peek = starter_llm_onboard_pending()
    assert peek["pending"] is True
    out = ensure_starter_llm_gateways()
    assert out["skipped"] == ["anthropic"]
    assert downloaded == ["cursor", "openai"]
