"""Closing a chat card survives restart without deleting workflow execution data."""
import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.automations import chat_cards
from frontend.ui_web.panel_api_automations import PanelApiAutomationsMixin


def test_dismissals_survive_reload_and_concurrent_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("frontend.app_paths.resolve_app_data_dir", lambda **_: tmp_path)
    pairs = [("chat-1", "run-1"), ("chat-1", "run-2"), ("chat-2", "run-1")]
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda pair: chat_cards.dismiss(*pair), pairs + pairs))
    importlib.reload(chat_cards)
    assert {(item["chat_id"], item["run_id"]) for item in chat_cards.dismissed()} == set(pairs)


def test_panel_snapshot_retains_events_and_includes_dismissals(tmp_path, monkeypatch):
    monkeypatch.setattr("frontend.app_paths.resolve_app_data_dir", lambda **_: tmp_path)
    events = [{"type": "workflow_run", "run": "run-1", "state": "done"}]
    monkeypatch.setattr("backend.automations.live_runs.snapshot", lambda: events)
    api = PanelApiAutomationsMixin()
    assert api.dismiss_workflow_run("chat-1", "run-1") == {"ok": True}
    assert api.workflow_run_snapshot() == {
        "ok": True, "events": events,
        "dismissed": [{"chat_id": "chat-1", "run_id": "run-1"}],
    }


@pytest.mark.parametrize("chat,run", [("", "r"), ("c", ""), ("c" * 257, "r")])
def test_invalid_ids_do_not_write(tmp_path, monkeypatch, chat, run):
    monkeypatch.setattr("frontend.app_paths.resolve_app_data_dir", lambda **_: tmp_path)
    with pytest.raises(ValueError):
        chat_cards.dismiss(chat, run)
    assert chat_cards.dismissed() == []
