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


def test_chat_permissions_popup_api_picks_modes_and_removes_rules(monkeypatch):
    from backend.tools.panel import permission_prompt as permissions
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    from frontend.ui_web.project_chats import create_conversation

    chat = create_conversation(PanelSettings.load(), "", title="Builder")
    api = PanelApiChatsMixin()
    state = api.get_agent_permissions(chat.id, "ducky")
    assert state["mode"] == "edits" and state["label"] == "Accept edits" and state["rules"] == []
    assert [m["id"] for m in state["modes"]] == ["ask", "edits", "all"]
    assert all(m["available"] for m in state["modes"])

    assert api.set_agent_permission_mode(chat.id, "ask", "ducky")["mode"] == "ask"
    assert api.get_agent_permissions(chat.id)["mode"] == "ask"  # the chat's own agent when none is named
    all_state = api.set_agent_permission_mode(chat.id, "all", "ducky")
    assert all_state["mode"] == "all" and permissions.allows_everything(chat.id)
    # The context panel's Turn off and the pop-up are one switch.
    assert api.set_agent_allow_everything(chat.id, False)["on"] is False
    assert api.get_agent_permissions(chat.id, "ducky")["mode"] == "edits"
    with pytest.raises(ValueError, match="Unknown permission mode"):
        api.set_agent_permission_mode(chat.id, "never", "ducky")

    permissions._remember(chat.id, "Bash:npm run test")
    permissions._remember(chat.id, "WebFetch")
    after = api.remove_agent_permission_rule(chat.id, "WebFetch", "ducky")
    assert after["rules"] == [{"rule": "Bash:npm run test", "label": "Bash: npm run test"}]
    api.set_agent_permission_mode(chat.id, "all", "ducky")
    cleared = api.clear_agent_permission_rules(chat.id, "ducky")
    assert cleared["rules"] == [] and cleared["mode"] == "all"


def test_chat_permissions_popup_api_refuses_a_mode_the_agent_cannot_use(monkeypatch):
    from backend.tools.panel import permission_prompt as permissions
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    from frontend.ui_web.project_chats import create_conversation

    monkeypatch.setattr(permissions, "_agent_registration", lambda _aid: {})
    chat = create_conversation(PanelSettings.load(), "", title="Codex chat")
    api = PanelApiChatsMixin()
    with pytest.raises(ValueError, match="doesn't ask for approval in Ducky"):
        api.set_agent_permission_mode(chat.id, "all", "codex")
    assert not permissions.allows_everything(chat.id)


def test_permission_revocation_reports_failed_persistence(monkeypatch):
    import pytest
    from backend.tools.panel import permission_prompt as permissions
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    monkeypatch.setattr(permissions, "_rules", lambda _: ["*"])
    monkeypatch.setattr(permissions, "_store_rules", lambda *_: None)
    with pytest.raises(ValueError, match="Could not save"):
        PanelApiChatsMixin().revoke_saved_agent_permission("chat", "*")
