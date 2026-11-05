"""Low memory never holds an agent turn: every start goes ahead, and one warning names the
biggest memory users."""

from __future__ import annotations

import pytest

from backend.util import proc_table
from frontend.ui_web import admission as adm

GB = 1024**3


@pytest.fixture
def pc(monkeypatch):
    """A fake PC: free RAM drops by each started agent's cost."""
    state = {"base": 20.0, "cost": 0.23, "started": []}
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_warned", {"low": False})
    monkeypatch.setattr(adm, "_role", lambda cid: "member")
    monkeypatch.setattr(adm, "min_free_gb", lambda: 6.0)
    monkeypatch.setattr(adm, "_read_free", lambda: state["base"] - state["cost"] * len(state["started"]))
    rows = {1: proc_table.Proc(1, 0, "UnrealEditorFortnite.exe", 0, 12 * GB, 1, 1, 0.0, 0.0),
            2: proc_table.Proc(2, 0, "claude.exe", 0, 1 * GB, 1, 1, 0.0, 0.0)}
    monkeypatch.setattr(proc_table, "table", lambda: rows)
    pushed: list[dict] = []

    def ask(cid):
        ok = adm.may_start(cid, "agent", push=pushed.append)
        if ok:
            state["started"].append(cid)
        return ok

    state["ask"], state["pushed"] = ask, pushed
    return state


def test_free_20_gb_starts_without_a_warning(pc):
    assert pc["ask"]("a") and pc["ask"]("b") and pc["ask"]("c")
    assert pc["pushed"] == []


def test_ten_members_on_3_gb_free_all_run_and_one_warning_is_posted(pc):
    pc["base"] = 3.0
    assert all(pc["ask"](f"m{i}") for i in range(10))
    assert len(pc["started"]) == 10 and all(adm.holds(f"m{i}") for i in range(10))
    assert len(pc["pushed"]) == 1
    text = pc["pushed"][0]["text"]
    assert "Waiting for memory" not in text
    assert "UnrealEditorFortnite.exe 12 GB" in text


def test_300_requested_at_25_gb_all_start_at_once(pc):
    pc["base"] = 25.0
    assert sum(pc["ask"](f"s{i}") for i in range(300)) == 300
    assert len(pc["pushed"]) == 1  # crossed the floor once: one warning
