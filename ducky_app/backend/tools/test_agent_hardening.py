"""Self-checks for agent-hardening helpers (spawn hints, settings filter, compact)."""

from __future__ import annotations

from pathlib import Path

from backend.agent.hard_rules import AGENT_HARD_RULES
from backend.agent.tools import compact_json_value
from backend.tools.actors import _verse_device_spawn_hint
from backend.tools.assets import _fortnite_ui_gallery_hint
from backend.tools.device_focused import _filter_settings, _resolve_actor_path


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


def test_hard_rules_require_project_copy_for_captures():
    assert "DuckyCaptures" in AGENT_HARD_RULES
    assert "AppData" in AGENT_HARD_RULES
    assert "MCP image" in AGENT_HARD_RULES or "vision" in AGENT_HARD_RULES


def test_mcp_instructions_require_followable_plans():
    from backend.server import mcp

    text = mcp.instructions or ""
    assert "ducky_create_plan" in text
    assert "Followable" in text or "followable" in text
    assert "thrash" in text


def test_enrich_screenshot_prefers_ducky_captures(tmp_path, monkeypatch):
    from backend.tools import editor as editor_mod

    project_root = tmp_path / "MyIsland"
    project_png = project_root / "Saved" / "Screenshots" / "uefn_ducky_screenshot.png"
    project_png.parent.mkdir(parents=True)
    project_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    class _Settings:
        uefn_project_root = str(project_root)

    monkeypatch.setattr(
        "frontend.settings.PanelSettings.load",
        staticmethod(lambda: _Settings()),
    )
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: tmp_path / "appdata",
    )
    monkeypatch.setattr(editor_mod, "_panel_media_server_reachable", lambda: True)
    out = editor_mod._enrich_screenshot({"path": str(project_png), "width": 1, "height": 1})
    assert "DuckyCaptures" in out["path"]
    assert Path(out["path"]).is_file()
    assert out["ue_screenshot_path"] == str(project_png)
    assert out["media_url"].startswith("http://")
    assert "tool_captures" in out["capture_path"].replace("\\", "/")


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


def test_vision_attachments_skip_failed_status(tmp_path):
    from backend.agent.capture_vision import vision_attachments_from_capture_result

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert (
        vision_attachments_from_capture_result(
            "take_high_res_screenshot",
            {"status": "timed_out", "path": str(png), "error": "timeout"},
        )
        == []
    )


def test_vision_attachments_uses_ue_screenshot_fallback(tmp_path):
    from backend.agent.capture_vision import vision_attachments_from_capture_result

    png = tmp_path / "Saved" / "Screenshots" / "ue.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    atts = vision_attachments_from_capture_result(
        "take_high_res_screenshot",
        {"status": "completed", "ue_screenshot_path": str(png)},
    )
    assert len(atts) == 1


def test_await_screenshot_polls_until_completed(monkeypatch):
    from backend.tools import editor as editor_mod

    calls: list[tuple[str, dict]] = []

    def fake_send(command, params=None, timeout=30.0):
        calls.append((command, dict(params or {})))
        if command == "take_high_res_screenshot":
            return {
                "status": "pending",
                "capture_id": "abc123",
                "filename": "shot_1.png",
                "width": 64,
                "height": 64,
            }
        if command == "poll_screenshot_capture":
            # Complete on second poll
            if sum(1 for c, _ in calls if c == "poll_screenshot_capture") >= 2:
                return {
                    "status": "completed",
                    "capture_id": "abc123",
                    "path": "C:/tmp/shot_1.png",
                    "filename": "shot_1.png",
                    "width": 64,
                    "height": 64,
                }
            return {"status": "pending", "capture_id": "abc123", "filename": "shot_1.png"}
        raise AssertionError(command)

    monkeypatch.setattr(editor_mod, "send_command", fake_send)
    monkeypatch.setattr(editor_mod, "_SCREENSHOT_POLL_INTERVAL_SEC", 0.0)
    out = editor_mod._await_screenshot_result(64, 64, "shot.png")
    assert out["status"] == "completed"
    assert out["path"].endswith("shot_1.png")
    assert calls[0][0] == "take_high_res_screenshot"
    assert any(c == "poll_screenshot_capture" for c, _ in calls)


def test_enrich_screenshot_warns_without_project_root(tmp_path, monkeypatch):
    from backend.tools import editor as editor_mod

    png = tmp_path / "Saved" / "Screenshots" / "uefn.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    class _Settings:
        uefn_project_root = ""

    monkeypatch.setattr(
        "frontend.settings.PanelSettings.load",
        staticmethod(lambda: _Settings()),
    )
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: tmp_path / "appdata",
    )
    monkeypatch.setattr(editor_mod, "_panel_media_server_reachable", lambda: False)
    out = editor_mod._enrich_screenshot({"path": str(png), "width": 1, "height": 1})
    assert out["path"] == str(png)
    assert "project_mirror_warning" in out
    assert out.get("media_url") in ("", None)
    assert "preview_error" in out


def test_enrich_screenshot_keeps_media_url_when_panel_up(tmp_path, monkeypatch):
    from backend.tools import editor as editor_mod

    project_root = tmp_path / "MyIsland"
    png = project_root / "Saved" / "Screenshots" / "uefn.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    class _Settings:
        uefn_project_root = str(project_root)

    monkeypatch.setattr(
        "frontend.settings.PanelSettings.load",
        staticmethod(lambda: _Settings()),
    )
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.resolve_app_data_dir",
        lambda for_write=False: tmp_path / "appdata",
    )
    monkeypatch.setattr(editor_mod, "_panel_media_server_reachable", lambda: True)
    out = editor_mod._enrich_screenshot({"path": str(png), "width": 1, "height": 1})
    assert "DuckyCaptures" in out["path"]
    assert out["media_url"].startswith("http://127.0.0.1:")
    assert "preview_error" not in out


if __name__ == "__main__":
    test_verse_device_spawn_hint_teleporter()
    test_verse_device_spawn_hint_ignores_normal_class()
    test_resolve_actor_path_label_alias()
    test_filter_settings_keys()
    test_compact_keeps_keyed_inspect()
    test_compact_strips_huge_unkeyed_inspect()
    test_fortnite_directory_hint()
    test_hard_rules_require_project_copy_for_captures()
    print("ok")
