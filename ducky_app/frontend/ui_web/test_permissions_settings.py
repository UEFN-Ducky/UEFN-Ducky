"""User permissions must round-trip through the same API used by General."""
import pytest
from frontend.settings import PanelSettings
from frontend.ui_web import panel_api as pa
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin


def test_ai_access_permissions_round_trip_without_touching_real_settings(monkeypatch):
    settings = PanelSettings(ai_ignore_strict=True)
    settings.uefn_project_root = ""
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    saved = []
    monkeypatch.setattr(pa.PanelSettings, "save", lambda self: saved.append(self))
    monkeypatch.setattr(pa, "apply_workspace_env", lambda _root: None)
    api = PanelApiSettingsMixin()
    expected = {
        "allow_settings_write": False,
        "allow_agent_clicks": True,
        "allow_see_uefn": False,
        "allow_see_other_programs": False,
    }
    assert api.save_agent_settings(expected).startswith("Saved")
    assert saved == [settings]
    result = api.get_settings()
    assert {key: result[key] for key in expected} == expected
    assert settings.ai_ignore_strict is True


@pytest.mark.parametrize("backend", ["db", "files"])
def test_saved_chat_permission_list_includes_all_rules_and_revokes_only_the_selected_rule(monkeypatch, backend):
    from backend.tools.panel import permission_prompt as permissions
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    from frontend.ui_web.project_chats import create_conversation

    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    first = create_conversation(PanelSettings.load(), "", title="First")
    second = create_conversation(PanelSettings.load(), "", title="Second")
    permissions._remember(first.id, "*")
    permissions._remember(first.id, "Bash:npm run test")
    permissions._remember(second.id, "Edit:some-folder")
    api = PanelApiChatsMixin()
    rows = api.list_saved_agent_permissions()
    assert {(row["title"], row["rule"]) for row in rows} == {
        ("First", "*"), ("First", "Bash:npm run test"), ("Second", "Edit:some-folder"),
    }
    assert api.revoke_saved_agent_permission(first.id, "*") == {"ok": True}
    assert permissions._rules(first.id) == ["Bash:npm run test"]
    assert permissions._rules(second.id) == ["Edit:some-folder"]
    assert len(api.list_saved_agent_permissions()) == 2


@pytest.mark.parametrize("backend", ["db", "files"])
def test_protection_switch_saves_through_the_real_ui_api_without_losing_file_rules(monkeypatch, backend):
    monkeypatch.setenv("DUCKY_STORE_BACKEND", backend)
    PanelSettings(ai_ignore_strict=True, ai_ignore_patterns=["private/"]).save()
    api = PanelApiSettingsMixin()
    assert api.save_agent_settings({"ai_ignore_strict": False}).startswith("Saved")
    fresh = PanelApiSettingsMixin().get_settings()
    assert fresh["ai_ignore_strict"] is False
    assert fresh["ai_ignore_patterns"] == ["private/"]


def test_permission_revocation_reports_failed_persistence(monkeypatch):
    import pytest
    from backend.tools.panel import permission_prompt as permissions
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    monkeypatch.setattr(permissions, "_rules", lambda _: ["*"])
    monkeypatch.setattr(permissions, "_store_rules", lambda *_: None)
    with pytest.raises(ValueError, match="Could not save"):
        PanelApiChatsMixin().revoke_saved_agent_permission("chat", "*")
