"""First-run popular plugin bundle — once, never auto-downloaded for existing installs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.starter_llm_gateways import (
    POPULAR_PLUGIN_SLUGS,
    ensure_starter_llm_gateways,
    starter_llm_onboard_pending,
    starter_setup_status,
)


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


def _write_plugin(root: Path, plugin_id: str, contributes: dict | None = None) -> None:
    folder = root / "UEFN-Ducky" / "uefn_plugins" / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "id": plugin_id,
        "kind": "plugin",
        "version": "1.0.0",
        "label": plugin_id,
        "default_enabled": True,
    }
    if contributes:
        payload["contributes"] = contributes
    (folder / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


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


def test_first_run_downloads_popular_bundle_once(isolated_appdata, monkeypatch) -> None:
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
    assert first["installed"] == list(POPULAR_PLUGIN_SLUGS)
    assert first["errors"] == []
    second = ensure_starter_llm_gateways()
    assert second["first_run"] is False
    assert downloaded == list(POPULAR_PLUGIN_SLUGS)


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
    assert downloaded == [slug for slug in POPULAR_PLUGIN_SLUGS if slug != "anthropic"]


def test_force_downloads_when_grandfathered(isolated_appdata, monkeypatch) -> None:
    _write_plugin(isolated_appdata, "verse")
    downloaded: list[str] = []

    def _fake_download(slug: str, **_kwargs):
        downloaded.append(slug)
        contributes = {"llm.providers": [{"id": slug}]} if slug == "openai" else None
        _write_plugin(isolated_appdata, slug, contributes)
        return {"ok": True, "kind": "plugin", "id": slug}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _fake_download)
    assert starter_llm_onboard_pending()["grandfathered"] is True
    blocked = ensure_starter_llm_gateways()
    assert blocked["first_run"] is False
    assert downloaded == []
    forced = ensure_starter_llm_gateways(force=True)
    assert forced["installed"] == [slug for slug in POPULAR_PLUGIN_SLUGS if slug != "verse"]
    assert "verse" in forced["skipped"]


def test_setup_status_detects_gateway_contrib(isolated_appdata) -> None:
    _write_plugin(isolated_appdata, "verse")
    none = starter_setup_status()
    assert none["gateway_ids"] == []
    assert none["pending_first_run"] is False

    _write_plugin(
        isolated_appdata,
        "openai",
        {"llm.providers": [{"id": "openai", "label": "OpenAI"}]},
    )
    hit = starter_setup_status()
    assert hit["gateway_ids"] == ["openai"]
    assert hit["pending_first_run"] is False


def test_setup_status_lists_the_bundle_with_what_is_installed(isolated_appdata) -> None:
    _write_plugin(isolated_appdata, "openai")
    _write_plugin(isolated_appdata, "verse")
    plugins = starter_setup_status()["plugins"]
    assert [p["slug"] for p in plugins] == list(POPULAR_PLUGIN_SLUGS)
    by_slug = {p["slug"]: p for p in plugins}
    assert by_slug["openai"] == {"slug": "openai", "label": "OpenAI", "group": "gateway", "installed": True}
    assert by_slug["anthropic"]["installed"] is False
    assert by_slug["verse"]["group"] == "editor" and by_slug["verse"]["installed"] is True
    assert by_slug["leveldesign"]["label"] == "Level Design"
