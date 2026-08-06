"""Self-checks for agent-hardening helpers (spawn hints, settings filter, compact)."""

from __future__ import annotations

from pathlib import Path

from backend.agent.hard_rules import AGENT_HARD_RULES
from backend.agent.tools import compact_json_value
from backend.tools.uefn.actors import _verse_device_spawn_hint
from backend.tools.uefn.assets import _fortnite_ui_gallery_hint
from backend.tools.uefn.device_focused import _filter_settings, _resolve_actor_path


def test_verse_device_spawn_hint_teleporter():
    hint = _verse_device_spawn_hint("teleporter_device")
    assert hint is not None
    assert "Verse API" in hint
    assert "search_assets" in hint
    assert "/Game/Creative" in hint
    assert "get_verse_api" in hint


def test_verse_device_spawn_hint_ignores_normal_class():
    assert _verse_device_spawn_hint("BP_Creative_Trigger_C") is None
    assert _verse_device_spawn_hint("") is None


def test_resolve_actor_path_label_alias():
    assert _resolve_actor_path("", "IslandSettings0") == "IslandSettings0"
    assert _resolve_actor_path("Pad1", "ignored") == "Pad1"


def test_filter_settings_keys():
    raw = {
        "settings": {
            "MaxPlayers": {"type": "int", "value": 5},
            "Noise": {"type": "bool", "value": True},
        }
    }
    out = _filter_settings(raw, ["MaxPlayers"])
    assert out["keys_filtered"] is True
    assert list(out["settings"]) == ["MaxPlayers"]
    assert "Noise" not in out["settings"]


def test_compact_keeps_keyed_inspect():
    value = {
        "label": "IslandSettings0",
        "keys_filtered": True,
        "settings": {f"k{i}": {"value": i} for i in range(20)},
    }
    out = compact_json_value("inspect_creative_device", value)
    assert out.get("keys_filtered") is True
    assert len(out["settings"]) == 20


def test_compact_strips_huge_unkeyed_inspect():
    value = {
        "label": "IslandSettings0",
        "settings": {f"k{i}": {"value": i} for i in range(20)},
    }
    out = compact_json_value("inspect_creative_device", value)
    assert "settings" not in out or len(out.get("settings", {})) <= 8
    assert "keys=" in (out.get("settings_note") or "")


def test_fortnite_directory_hint():
    assert _fortnite_ui_gallery_hint("/Fortnite") is not None
    assert "/Game/Creative" in (_fortnite_ui_gallery_hint("/Fortnite/") or "")
    assert _fortnite_ui_gallery_hint("/Game/Creative") is None
    assert "content_catalog" in AGENT_HARD_RULES


def test_hard_rules_captures_use_appdata_not_project():
    assert "tool_captures" in AGENT_HARD_RULES
    assert "LOCALAPPDATA" in AGENT_HARD_RULES or "AppData" in AGENT_HARD_RULES
    assert "MCP image" in AGENT_HARD_RULES or "vision" in AGENT_HARD_RULES
    assert ".ducky" in AGENT_HARD_RULES
    assert "Never" in AGENT_HARD_RULES or "never" in AGENT_HARD_RULES


def test_hard_rules_forbid_project_side_storage_except_ducky():
    assert "Project folder storage" in AGENT_HARD_RULES
    assert ".ducky" in AGENT_HARD_RULES
    assert "DuckyCaptures" in AGENT_HARD_RULES  # named as forbidden
    assert "LOCALAPPDATA" in AGENT_HARD_RULES or "AppData" in AGENT_HARD_RULES


def test_hard_rules_forbid_invented_game_asset_paths():
    assert "Project assets only" in AGENT_HARD_RULES
    assert "/Game/Materials" in AGENT_HARD_RULES
    assert "content_root" in AGENT_HARD_RULES


def test_hard_rules_forbid_persist_weak_map_key_removal():
    assert "weak_map" in AGENT_HARD_RULES
    assert "persistence" in AGENT_HARD_RULES
    assert "never remove" in AGENT_HARD_RULES.lower()


def test_hard_rules_forbid_digest_mutation():
    assert "digest" in AGENT_HARD_RULES.lower()
    assert "READ-ONLY" in AGENT_HARD_RULES or "read-only" in AGENT_HARD_RULES.lower()
    assert "*.digest.verse" in AGENT_HARD_RULES or ".digest.verse" in AGENT_HARD_RULES
    assert "workspace_compile_verse" in AGENT_HARD_RULES
    assert "auto-edit" in AGENT_HARD_RULES.lower() or "auto-edits" in AGENT_HARD_RULES.lower()


def test_digest_path_guard_blocks_writes():
    from backend.tools.verse.verse_digests import is_uefn_digest_path, require_not_digest_path

    assert is_uefn_digest_path("Fortnite.digest.verse")
    assert is_uefn_digest_path(r"C:\AppData\VerseProject\P\Assets\Assets.digest.verse")
    assert is_uefn_digest_path("Verse/Verse.digest.verse")
    assert not is_uefn_digest_path("Content/Verse/Economy/economy_manager.verse")
    try:
        require_not_digest_path("UnrealEngine.digest.verse")
        raise AssertionError("expected ValueError for digest write")
    except ValueError as exc:
        assert "READ-ONLY" in str(exc)


def test_hard_rules_require_ask_user_questionnaire():
    # Ask-user questionnaire guidance lives in the embedded agent prompt body
    # (not the slim IDE-facing AGENT_HARD_RULES string).
    from backend.agent.prompt import _rules_body

    text = _rules_body(4200)
    assert "ducky_ask_user" in text
    assert "Your call" in text
    assert "questionnaire" in text.lower() or "composer" in text.lower()


def test_mcp_instructions_require_followable_plans():
    from backend.agent.prompt import _rules_body
    from backend.server import mcp

    text = mcp.instructions or ""
    assert "ducky_create_plan" in text
    assert "Followable" in text or "followable" in text
    assert "thrash" in text
    # Ask-user is enforced in the embedded prompt; plans are in MCP instructions.
    assert "ducky_ask_user" in _rules_body(4200)


def test_enrich_screenshot_uses_appdata_not_project(tmp_path, monkeypatch):
    from backend.tools.uefn import editor as editor_mod

    project_root = tmp_path / "MyIsland"
    project_png = project_root / "Saved" / "Screenshots" / "uefn_ducky_screenshot.png"
    project_png.parent.mkdir(parents=True)
    project_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    appdata = tmp_path / "appdata"

    class _Settings:
        uefn_project_root = str(project_root)

    monkeypatch.setattr(
        "frontend.settings.PanelSettings.load",
        staticmethod(lambda: _Settings()),
    )
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: appdata,
    )
    out = editor_mod._enrich_screenshot({"path": str(project_png), "width": 1, "height": 1})
    assert "tool_captures" in out["path"].replace("\\", "/")
    assert "DuckyCaptures" not in out["path"]
    assert Path(out["path"]).is_file()
    assert out["ue_screenshot_path"] == str(project_png)
    assert out["media_url"].startswith("http://")
    assert not (project_root / "Saved" / "DuckyCaptures").exists()


def test_wait_for_screenshot_file_polls_until_ready(tmp_path, monkeypatch):
    from backend.tools.uefn import editor as editor_mod

    png = tmp_path / "shot.png"
    state = {"n": 0}

    def _fake_sleep(_sec: float) -> None:
        state["n"] += 1
        if state["n"] >= 2:
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    monkeypatch.setattr(editor_mod.time, "sleep", _fake_sleep)
    monkeypatch.setattr(editor_mod, "_SCREENSHOT_FILE_WAIT_SEC", 2.0)
    out = editor_mod._wait_for_screenshot_file(
        {"path": str(png), "await_path": True, "hint": "pending"}
    )
    assert out["path"] == str(png)
    assert "await_path" not in out
    assert png.is_file()


def test_wait_for_screenshot_file_errors_when_missing(tmp_path, monkeypatch):
    from backend.tools.uefn import editor as editor_mod

    monkeypatch.setattr(editor_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(editor_mod, "_SCREENSHOT_FILE_WAIT_SEC", 0.01)
    missing = tmp_path / "missing.png"
    out = editor_mod._wait_for_screenshot_file({"path": str(missing), "await_path": True})
    assert "error" in out
    assert "not ready" in out["error"]


def test_vision_attachments_from_capture_result(tmp_path):
    from backend.agent.capture_vision import vision_attachments_from_capture_result

    png = tmp_path / "Saved" / "DuckyCaptures" / "shot.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    atts = vision_attachments_from_capture_result(
        "take_high_res_screenshot",
        {"ok": True, "path": str(png)},
    )
    assert len(atts) == 1
    assert atts[0].kind == "image"
    assert atts[0].data_base64
    assert vision_attachments_from_capture_result("spawn_actor", {"path": str(png)}) == []


if __name__ == "__main__":
    test_verse_device_spawn_hint_teleporter()
    test_verse_device_spawn_hint_ignores_normal_class()
    test_resolve_actor_path_label_alias()
    test_filter_settings_keys()
    test_compact_keeps_keyed_inspect()
    test_compact_strips_huge_unkeyed_inspect()
    test_fortnite_directory_hint()
    test_hard_rules_captures_use_appdata_not_project()
    test_hard_rules_forbid_project_side_storage_except_ducky()
    test_hard_rules_forbid_digest_mutation()
    test_digest_path_guard_blocks_writes()
    print("ok")
