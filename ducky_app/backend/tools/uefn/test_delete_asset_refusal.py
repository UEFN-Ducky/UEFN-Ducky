"""A delete the editor will not do answers in one line with the next move, and says so when
it is asked again with nothing changed (Nov 11 2026: 46 refused delete_asset calls in a day)."""

from __future__ import annotations

import json

import pytest

from backend.tools.uefn import assets
from frontend import usage_stats

PATH = "/MyIsland/Props/Crate"
REFUSAL = (
    "UEFN command 'delete_asset' failed: Refused: /MyIsland/Props/Crate is still used by 2 asset(s): "
    "/MyIsland/Maps/Main, /MyIsland/Props/Stack. Removing it would break them — relink / fixup_redirectors "
    "first. To remove a whole character folder, call delete_project_folder.\nTraceback (most recent call last):\n  File ..."
)


@pytest.fixture
def editor(monkeypatch):
    state = {"calls": [], "reply": RuntimeError(REFUSAL)}

    def send(command, params=None, **_kw):
        state["calls"].append((command, params))
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"]

    monkeypatch.setattr(assets, "send_command", send)
    monkeypatch.setattr(assets, "_said_no", {})
    monkeypatch.setattr(assets, "_lore_after_gone", lambda path: {"skipped": "no_project"})
    return state


def test_a_refusal_is_one_line_with_the_reason_and_no_traceback(editor) -> None:
    raw = assets.delete_asset(PATH)
    out = json.loads(raw)
    assert out["ok"] is False and out["fail"] == "refused"
    assert out["error"].startswith("/MyIsland/Props/Crate is still used by 2 asset(s): /MyIsland/Maps/Main")
    assert "Traceback" not in raw and "Relink or fix what still uses it" in out["hint"]
    assert usage_stats.fail_of(raw) == "refused"  # counted as a rule's no, not a broken tool


def test_asked_again_the_editor_is_still_asked_and_the_answer_says_nothing_changed(editor) -> None:
    assets.delete_asset(PATH)
    out = json.loads(assets.delete_asset(PATH))
    assert len(editor["calls"]) == 2  # the asset may have been relinked since: always re-check
    assert out["hint"].startswith("Asked 2 times, same answer: nothing changed")
    assert json.loads(assets.delete_asset(PATH.upper() + "/"))["hint"].startswith("Asked 3 times")


def test_a_delete_that_works_after_a_relink_goes_through_and_resets_the_count(editor) -> None:
    assets.delete_asset(PATH)
    editor["reply"] = {"ok": True, "kind": "asset", "removed": PATH}
    out = json.loads(assets.delete_asset(PATH))
    assert out["ok"] is True and out["removed"] == PATH
    editor["reply"] = RuntimeError(REFUSAL)
    assert "Asked" not in json.loads(assets.delete_asset(PATH))["hint"]


def test_an_old_refusal_is_not_counted_as_asking_again(editor) -> None:
    first = assets._no_answer(PATH, REFUSAL, now=100.0)
    later = assets._no_answer(PATH, REFUSAL, now=100.0 + assets._SAME_ANSWER_S + 1)
    assert "Asked" not in first["hint"] and "Asked" not in later["hint"]


def test_uefn_closed_or_a_timeout_stays_an_error(editor) -> None:
    editor["reply"] = RuntimeError("UEFN command 'delete_asset' failed: editor crashed")
    with pytest.raises(RuntimeError):
        assets.delete_asset(PATH)
    editor["reply"] = ConnectionError("UEFN listener not reachable on port 4200")
    with pytest.raises(ConnectionError):
        assets.delete_asset(PATH)
