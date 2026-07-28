"""Stop → follow-up must cancel the live run instead of rejecting the send."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frontend.ui_web import agent_modes


def test_run_message_cancels_live_run_before_starting_follow_up():
    pushes: list[dict] = []
    running = {"live": True}
    conv = SimpleNamespace(
        id="chat-1",
        title="t",
        messages=[],
        model="gpt-test",
        provider="openai",
        coding_agent="ducky",
        folder_id=None,
        file_path="",
    )

    def fake_is_running(_cid: str) -> bool:
        return running["live"]

    def fake_cancel(_cid=None) -> None:
        running["live"] = False

    def fake_wait(_cid: str, _timeout: float = 2.0) -> bool:
        running["live"] = False
        return True

    with (
        patch.object(agent_modes, "_in_bridge_process", return_value=False),
        patch.object(agent_modes, "is_agent_running", side_effect=fake_is_running),
        patch.object(agent_modes, "cancel_agent", side_effect=fake_cancel) as cancel,
        patch.object(agent_modes, "wait_for_idle", side_effect=fake_wait) as wait,
        patch.object(agent_modes, "load_conversation", return_value=conv),
        # Bail after the cancel gate — we only assert Stop→continue unwinds the old run.
        patch.object(agent_modes.PanelSettings, "load", side_effect=RuntimeError("stop-after-cancel")),
    ):
        try:
            agent_modes.run_message(
                "chat-1",
                "fix spacing",
                "agent",
                "gpt-test",
                push=pushes.append,
            )
        except RuntimeError as e:
            assert str(e) == "stop-after-cancel"

    assert cancel.called
    assert wait.called
    assert running["live"] is False
