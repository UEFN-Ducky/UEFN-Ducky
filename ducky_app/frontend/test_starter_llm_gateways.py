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
    _write_plugin(isolated_appdata, "uefn")
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
    _write_plugin(isolated_appdata, "uefn")
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
    assert forced["installed"] == [slug for slug in POPULAR_PLUGIN_SLUGS if slug != "uefn"]
    assert "uefn" in forced["skipped"]


def test_setup_status_detects_gateway_contrib(isolated_appdata) -> None:
    _write_plugin(isolated_appdata, "uefn")
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
    _write_plugin(isolated_appdata, "uefn")
    plugins = starter_setup_status()["plugins"]
    assert [p["slug"] for p in plugins] == list(POPULAR_PLUGIN_SLUGS)
    by_slug = {p["slug"]: p for p in plugins}
    assert by_slug["openai"] == {"slug": "openai", "label": "OpenAI", "group": "ai", "installed": True}
    assert by_slug["anthropic"]["installed"] is False and by_slug["google"]["group"] == "ai"
    assert by_slug["uefn"]["group"] == "uefn" and by_slug["uefn"]["installed"] is True
    assert by_slug["uefn"]["label"] == "UEFN" and "leveldesign" not in by_slug
    assert by_slug["playtester"]["group"] == "uefn" and by_slug["typesafe"]["group"] == "uefn"
    assert {s for s, p in by_slug.items() if p["group"] == "create"} == {"blender", "forge", "meshy", "tripo", "adobe", "island-media-kit"}
    assert by_slug["piper"]["group"] == "extras" and by_slug["piper"]["label"] == "Pipe voices"
    # New users always get the in-app browser and the in-game play tester.
    assert by_slug["browser"]["group"] == "extras" and by_slug["browser"]["label"] == "Web Browser"
    assert by_slug["playtester"]["label"] == "Play Tester"
    # The account (Ducky AI) and the gateways install first, so Connect AI can start while the rest land.
    assert [p["slug"] for p in plugins][:5] == ["account", "anthropic", "openai", "google", "cursor"]
    assert {p["group"] for p in plugins[:5]} == {"ai"} and by_slug["account"]["label"] == "Ducky Account"


def test_setup_turns_on_a_plugin_that_ships_off(isolated_appdata, monkeypatch) -> None:
    turned_on: list[str] = []

    def _fake_download(slug: str, **_kwargs):
        _write_plugin(isolated_appdata, slug)
        return {"ok": True, "kind": "plugin", "id": slug, "enabled": slug != "piper"}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _fake_download)
    monkeypatch.setattr(
        "backend.uefn_plugins.store.set_uefn_plugin_enabled",
        lambda slug, enabled, **_kw: turned_on.append(slug) or {"ok": True},
    )
    out = ensure_starter_llm_gateways()
    assert "piper" in out["installed"]
    assert turned_on == ["piper"]


def _welcome_api(monkeypatch):
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    opened: list[str] = []
    monkeypatch.setattr(pa, "notify_chats_changed", lambda conv_id="", *a, **k: opened.append(conv_id))
    return PanelApiChatsMixin(), opened


def test_the_setup_stays_open_until_finish_then_makes_the_welcome_ducky_once(isolated_appdata, monkeypatch) -> None:
    from frontend.starter_llm_gateways import WELCOME_DUCKY_TITLE, WELCOME_DUCKY_TOURS
    from frontend.ui_web.panel_api import _messages_to_ui, load_conversation

    def _fake_download(slug: str, **_kwargs):
        _write_plugin(isolated_appdata, slug, {"llm.providers": [{"id": slug}]} if slug == "openai" else None)
        return {"ok": True, "kind": "plugin", "id": slug}

    monkeypatch.setattr("frontend.duckyos_account.store_download_and_install", _fake_download)
    assert starter_setup_status()["pending_first_run"] is True  # a fresh install
    ensure_starter_llm_gateways(force=True)
    # Plugins in (and seeded) is not finished: quitting now reopens the setup on the next start.
    assert starter_llm_onboard_pending()["pending"] is False
    assert starter_setup_status()["pending_first_run"] is True

    api, opened = _welcome_api(monkeypatch)
    made = api.finish_first_run_setup()
    assert made["ok"] and made["created"] and made["title"] == WELCOME_DUCKY_TITLE
    assert opened == [made["id"]]  # announced (tab opens) with its message already in it
    assert starter_setup_status()["pending_first_run"] is False

    conv = load_conversation(made["id"])
    rows = _messages_to_ui(conv)
    assert [r["role"] for r in rows] == ["assistant"]
    assert rows[0]["tours"] == list(WELCOME_DUCKY_TOURS) and "chat.composer" in rows[0]["tours"]
    assert "walkthrough" in rows[0]["text"].lower()

    assert api.finish_first_run_setup() == {"ok": True, "created": False}  # a second Finish
    assert opened == [made["id"]]


def test_a_restart_resumes_the_setup_step_then_the_first_run_tour_step_until_it_ends(isolated_appdata, monkeypatch) -> None:
    from frontend.starter_llm_gateways import save_setup_progress

    assert starter_setup_status()["setup_step"] == "welcome"  # a fresh install
    for step in ("plugins", "ai", "defaults", "rating"):
        assert save_setup_progress(step) is True
        status = starter_setup_status()  # what the next start reads back
        assert status["pending_first_run"] is True and status["setup_step"] == step
    assert save_setup_progress("3") is False  # no tour while the setup is open

    api, _opened = _welcome_api(monkeypatch)
    made = api.finish_first_run_setup()  # the rating was submitted
    status = starter_setup_status()
    assert status["pending_first_run"] is False and "setup_step" not in status
    assert status["first_run_tour"] == {"step": 0, "chat_id": made["id"]}
    assert save_setup_progress("plugins") is False  # the setup never comes back
    assert save_setup_progress("4") is True
    assert starter_setup_status()["first_run_tour"] == {"step": 4, "chat_id": made["id"]}

    from frontend.settings import PanelSettings

    s = PanelSettings.load()  # finished or skipped: the walkthrough's own completion flag
    s.walkthrough_completed = {**(s.walkthrough_completed or {}), "first.run": True}
    s.save()
    status = starter_setup_status()
    assert status["pending_first_run"] is False and "first_run_tour" not in status
    assert save_setup_progress("welcome") is False and save_setup_progress("2") is False
    assert starter_setup_status()["pending_first_run"] is False


def test_an_install_that_already_used_ducky_gets_no_setup_and_no_welcome_ducky(isolated_appdata, monkeypatch) -> None:
    _write_plugin(isolated_appdata, "uefn")
    assert starter_setup_status()["pending_first_run"] is False
    api, opened = _welcome_api(monkeypatch)
    assert api.finish_first_run_setup() == {"ok": True, "created": False}
    assert opened == []
