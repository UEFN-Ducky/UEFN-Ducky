"""Agent dropdown assignments must select the actual runtime target."""

from unittest.mock import Mock

import pytest

from backend.automations import runner
from frontend.settings import PanelSettings
from frontend.ui_web.agent_turns.testing import patch_turns
from frontend.ui_web.chats.conversations import create_conversation, load_conversation
from frontend.ui_web.chats.saves import save_conversation


def test_agent_catalog_uses_picker_and_optional_instructions():
    from backend.automations.catalog import BUILTIN_NODES

    node = next(row for row in BUILTIN_NODES if row["type"] == "pipeline.agent")
    assert [field["id"] for field in node["config_fields"]] == ["ducky", "prompt", "allow_web", "archive_if"]
    assert node["config_fields"][0]["type"] == "ducky"


def test_existing_assignment_runs_selected_chat_and_returns_result(monkeypatch):
    conv = create_conversation(PanelSettings.load(), "", title="Existing artist", model="its-model", provider="its-provider")
    wait = Mock(return_value={"status": "done", "assistant_text": "Finished"})
    spawn = Mock(side_effect=AssertionError("Existing ducky must not spawn a replacement"))
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)
    monkeypatch.setattr(runner, "_seat_agent_cluster", spawn)
    monkeypatch.setattr(runner, "_create_pipeline_ducky", spawn)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: False)

    out = runner._pipeline_agent({"ducky": f"chat:{conv.id}"}, {"prompt": "Build this", "caller_conv_id": "caller"})

    assert out["ok"] is True
    assert out["result"]["conv_id"] == conv.id
    assert out["result"]["text"] == "Finished"
    assert wait.call_args.args == (conv.id, "Build this", "agent", "")
    assert wait.call_args.kwargs["parent"] == ""
    saved = load_conversation(conv.id)
    assert (saved.model, saved.provider, saved.folder_id, saved.parent_conv_id) == ("its-model", "its-provider", "", "")


@pytest.mark.parametrize("reason", ["deleted", "group", "caller", "busy"])
def test_invalid_or_busy_existing_assignment_stops_without_sending(monkeypatch, reason):
    conv = create_conversation(PanelSettings.load(), "", title="Assigned")
    conv.is_group = reason == "group"
    save_conversation(conv)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: reason == "busy")
    wait = Mock(side_effect=AssertionError("Cannot send to this ducky"))
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)

    out = runner._pipeline_agent(
        {"ducky": "chat:missing" if reason == "deleted" else f"chat:{conv.id}"},
        {"caller_conv_id": conv.id if reason == "caller" else "other"},
    )

    assert out["ok"] is False
    assert out["error"]
    wait.assert_not_called()


@pytest.mark.parametrize("choice", [None, "__new__", "__blank__"])
def test_create_at_runtime_uses_default_model_and_creates_fresh_ducky_each_run(monkeypatch, choice):
    from frontend.favorite_models import FavoriteSelection, ResolveOk

    resolve = Mock(return_value=ResolveOk("ducky", "default-model", "provider", FavoriteSelection("provider", "default-model")))
    monkeypatch.setattr("frontend.ui_web.panel_api.resolve_model_selection", resolve)
    patch_turns(monkeypatch, "notify_chats_changed", Mock())
    monkeypatch.setattr(runner, "_resolve_profile", Mock(side_effect=AssertionError("No profile is required")))
    wait = Mock(return_value={"status": "done", "assistant_text": "Done"})
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)
    cfg = {"ducky": choice, "profile_id": "old-profile"} if choice else {}
    payload = {"prompt": "Do the workflow task"}
    if choice:
        payload["ducky"] = "incoming-profile"

    first = runner._pipeline_agent(cfg, payload)
    second = runner._pipeline_agent(cfg, payload)

    assert first["ok"] and second["ok"]
    assert first["result"]["conv_id"] != second["result"]["conv_id"]
    conv = load_conversation(first["result"]["conv_id"])
    assert (conv.model, conv.provider, conv.coding_agent) == ("default-model", "provider", "ducky")
    assert resolve.call_args.args[0] is None
    assert wait.call_args.args[1] == "Do the workflow task"


@pytest.mark.parametrize("picked, sent", [("codex:gpt-6-sol", "gpt-6-sol"), ("claude_code:claude-opus-5-5", "claude-opus-5-5"),
                                           ("its-model", "its-model")])
def test_a_picked_model_reaches_the_run_without_its_gateway_prefix(monkeypatch, picked, sent):
    """The picker's "codex:gpt-6-sol" picks the ducky's gateway; the CLI only takes "gpt-6-sol"."""
    monkeypatch.setattr("frontend.favorite_models.known_backends", lambda: {"codex", "claude_code"})
    monkeypatch.setattr(runner, "_create_pipeline_ducky", lambda cfg, payload: {"ok": True, "conv_id": "worker"})
    wait = Mock(return_value={"status": "done", "assistant_text": "Done"})
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)

    assert runner._pipeline_agent({"ducky": "", "model": picked, "prompt": "Draw"}, {})["ok"]
    assert wait.call_args.args[3] == sent


def test_create_at_runtime_reports_model_configuration_error(monkeypatch):
    from frontend.favorite_models import ResolveErr

    monkeypatch.setattr("frontend.ui_web.panel_api.resolve_model_selection", lambda *a: ResolveErr("missing", "Choose a default model in Settings."))
    create = Mock(side_effect=AssertionError("Must resolve the model before creating a chat"))
    monkeypatch.setattr("frontend.ui_web.chats.conversations.create_conversation", create)
    out = runner._pipeline_agent({"ducky": "__new__"}, {})
    assert out == {"ok": False, "error": "Choose a default model in Settings."}
    create.assert_not_called()


def test_saved_profile_still_spawns_with_its_settings(monkeypatch):
    profile = {"id": "artist", "name": "Artist"}
    kwargs = {"model": "profile-model", "ducky_name": "Artist"}
    resolve = Mock(return_value=profile)
    seat = Mock(return_value={"ok": True, "conv_id": "worker", "group_id": "group"})
    wait = Mock(return_value={"status": "done", "assistant_text": "Painted"})
    monkeypatch.setattr(runner, "_resolve_profile", resolve)
    monkeypatch.setattr(runner, "_agent_spawn_kwargs", lambda p: kwargs)
    monkeypatch.setattr(runner, "_seat_agent_cluster", seat)
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)
    cfg = {"ducky": "artist", "prompt": "Paint this"}
    payload = {"caller_conv_id": "caller"}

    out = runner._pipeline_agent(cfg, payload)

    assert out["ok"] is True
    resolve.assert_called_once_with("artist")
    seat.assert_called_once_with(cfg, payload, profile, kwargs)
    assert wait.call_args.args == ("worker", "Paint this", "agent", "profile-model")
    assert wait.call_args.kwargs["parent"] == "caller"


def test_profile_invite_resolves_its_own_model_without_bare_id_override(monkeypatch):
    api = Mock()
    api.group_seat_profile.return_value = {"ok": True, "member": {"member_conv_id": "worker"}}
    monkeypatch.setattr("backend.tools.panel.ducky_panel._panel_api", lambda: api)

    out = runner._seat_agent_cluster(
        {}, {"group_id": "run-group"}, {"id": "artist", "name": "Artist"}, {"model": "bare-model-id"}
    )

    assert out["ok"] is True
    assert out["group_id"] == "run-group"
    api.group_create.assert_not_called()
    api.group_seat_profile.assert_called_once_with("run-group", "artist", model="")


def test_an_agent_step_may_use_the_run_s_folders_only_while_it_works(monkeypatch, tmp_path):
    # Oct 17 2026: a trailer workflow's steps could not open <Output root>/<run>/bible.json.
    from backend.workspace.run_folders import granted

    out = tmp_path / "Ducky Trailers"
    out.mkdir()
    conv = create_conversation(PanelSettings.load(), "", title="Concept artist")
    seen = {}

    def wait(conv_id, *_a, **_k):
        seen["during"] = granted(conv_id)
        return {"status": "done", "assistant_text": "Done"}

    monkeypatch.setattr(runner, "_run_message_and_wait", wait)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: False)
    out_ok = runner._pipeline_agent(
        {"ducky": f"chat:{conv.id}"}, {"prompt": "Read bible.json", "caller_conv_id": "caller", "_run_folders": [str(out)]}
    )
    assert out_ok["ok"] is True
    assert seen["during"] == [str(out)]
    assert granted(conv.id) == []
