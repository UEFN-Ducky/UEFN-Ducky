"""The agent_turns test helpers swap every copy of a name, and put them all back."""

from __future__ import annotations

import pytest

from frontend.ui_web.agent_turns import pending, routing, state
from frontend.ui_web.agent_turns.testing import holders, patch_turns, turns_patched


def test_holders_lists_every_module_that_binds_the_name() -> None:
    assert {state, pending, routing} <= set(holders("is_agent_running"))


def test_an_unknown_name_is_refused() -> None:
    with pytest.raises(AttributeError):
        holders("no_such_name_anywhere")


def test_patch_turns_reaches_every_copy(monkeypatch) -> None:
    real = state.is_agent_running

    def fake(conv_id: str) -> bool:
        return True

    patch_turns(monkeypatch, "is_agent_running", fake)
    assert state.is_agent_running is pending.is_agent_running is routing.is_agent_running is fake
    monkeypatch.undo()
    assert state.is_agent_running is pending.is_agent_running is routing.is_agent_running is real


def test_turns_patched_shares_one_mock_and_restores() -> None:
    real = state.close_changeset_run
    with turns_patched("close_changeset_run", return_value=None) as fake:
        assert state.close_changeset_run is routing.close_changeset_run is fake
    assert state.close_changeset_run is routing.close_changeset_run is real
