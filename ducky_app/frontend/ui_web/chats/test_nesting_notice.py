"""Each agent whose team a move changed is told once, plainly (nesting_notice)."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import group_orchestrator
from frontend.ui_web.chats import conversations as _convs
from frontend.ui_web.chats import groups, nesting_notice
from frontend.ui_web.chats import saves as _saves

_NAMES = {"R": "Release 1.2.469", "M": "Memory fix 1.2.470", "lead": "Developer", "me": "Lead: Memory fix",
          "A": "Team A", "B": "Team B", "la": "Lead A", "lb": "Lead B", "m": "Verse Coder"}


def _notes(moves, titles=None, steps=None):
    return nesting_notice.team_notices(
        moves, name_of=lambda cid: _NAMES.get(cid, cid),
        plan_title_of=lambda cid: (titles or {}).get(cid, ""),
        open_steps_of=lambda lead, cid: (steps or {}).get((lead, cid), []),
    )


def test_a_move_between_groups_tells_the_mover_and_each_lead_once():
    moves = {"m": {"left": "A", "joined": "B", "leader_changed": False, "team": "B",
                   "old_leader": "la", "new_leader": "lb"}}
    notes = _notes(moves, titles={"m": "Team B plan"}, steps={("la", "m"): ["a1", "a2"]})
    assert [to for to, _ in notes] == ["m", "la", "lb"]
    assert notes[0][1] == ('[ducky:team] You left team "Team A" (lead Lead A). You are now in "Team B" '
                           '(lead Lead B). Your plan is now: Team B plan.')
    assert notes[1][1] == '[ducky:team] "Verse Coder" left your team "Team A"; its open steps: a1, a2.'
    assert notes[2][1] == '[ducky:team] "Verse Coder" joined your team "Team B".'


def test_a_group_dragged_to_the_top_level_tells_its_own_lead_and_the_old_outer_lead():
    moves = {"RUST": {"left": "IMK", "joined": "", "leader_changed": False, "team": "",
                      "old_leader": "imk", "new_leader": "", "group_lead": "rl"}}
    names = {"RUST": "Rust launcher team", "IMK": "Island Media Kit", "imk": "IMK producer", "rl": "Lead: Rust"}
    notes = nesting_notice.team_notices(moves, name_of=lambda c: names.get(c, c),
                                        plan_title_of=lambda c: "", open_steps_of=lambda lead, c: [])
    assert notes == [
        ("rl", '[ducky:team] Your team "Rust launcher team" moved out of "Island Media Kit" to the top level; '
               "you report to nobody: you are the top lead."),
        ("imk", '[ducky:team] Team "Rust launcher team" (lead Lead: Rust) left your team "Island Media Kit"; '
                "it no longer reports to you."),
    ]


def test_a_group_dragged_into_another_group_tells_its_lead_who_it_reports_to():
    moves = {"N": {"left": "A", "joined": "B", "leader_changed": False, "team": "B",
                   "old_leader": "la", "new_leader": "lb", "group_lead": "nl"}}
    notes = _notes(moves)
    assert [to for to, _ in notes] == ["nl", "la", "lb"]
    assert notes[0][1] == '[ducky:team] Your team "N" moved out of "Team A" into "Team B"; you report to Lead B.'
    assert notes[2][1] == '[ducky:team] Team "N" (lead nl) joined your team "Team B"; it reports to you.'


def test_a_note_names_the_new_plan_or_none():
    moves = {"me": {"left": "R", "joined": "M", "leader_changed": True, "team": "M",
                    "old_leader": "lead", "new_leader": "me"}}
    notes = _notes(moves)
    assert notes[0] == ("me", '[ducky:team] You left team "Release 1.2.469" (lead Developer). '
                              'You now lead "Memory fix 1.2.470". Your plan is now: none: make your own.')
    assert [to for to, _ in notes] == ["me", "lead"]  # the new lead is the mover: one note
    titled = _notes(moves, titles={"me": "1.2.470 Memory fix"})
    assert titled[0][1].endswith("Your plan is now: 1.2.470 Memory fix.")


def test_open_steps_are_the_unfinished_steps_the_plan_gives_that_chat():
    plan = {"nodes": [
        {"id": "s", "assignee": "m", "children": [
            {"id": "s1", "status": "completed"}, {"id": "s2", "status": "pending"}]},
        {"id": "x", "assignee": "other", "status": "pending"},
        {"id": "y", "assignee": "m", "status": "in_progress"},
    ]}
    assert nesting_notice.open_steps(plan, "m") == ["s2", "y"]
    assert nesting_notice.open_steps(None, "m") == []


def test_a_move_inside_the_same_group_sends_nothing(monkeypatch):
    folders = [SimpleNamespace(id="fA", name="A", parent_id="", group_hub_id="A"),
               SimpleNamespace(id="fA-sub", name="sub", parent_id="fA", group_hub_id="")]

    def conv(cid, folder, **kw):
        base = dict(id=cid, title=cid, folder_id=folder, parent_conv_id="A", is_group=False, leader_conv_id="",
                    group_members=[], ducky_name="", ducky_style="", model="", coding_agent="", profile_id="")
        return SimpleNamespace(**{**base, **kw})

    convs = {"A": conv("A", "fA", parent_conv_id="", is_group=True, leader_conv_id="la",
                       group_members=[{"member_conv_id": "la"}, {"member_conv_id": "m"}]),
             "la": conv("la", "fA"), "m": conv("m", "fA-sub")}  # dragged into a plain subfolder
    monkeypatch.setattr(groups, "island_for_conversation", lambda cid, root=None: (list(convs.values()), folders))
    monkeypatch.setattr(_convs, "load_conversation", lambda cid, root=None: convs.get(cid))
    monkeypatch.setattr(_saves, "save_conversation", lambda c, root=None, **kw: None)
    monkeypatch.setattr(_saves, "flush_chat_saves", lambda *a, **kw: True)
    monkeypatch.setattr(group_orchestrator, "save_group_roster", lambda c, rows, root=None, **kw: None)
    sent: list = []
    monkeypatch.setattr(nesting_notice, "_deliver", lambda to, note: sent.append((to, note)))

    assert groups.sync_membership(None, ["m"]) == {}
    assert sent == []

    convs["m"].folder_id = ""  # now out of the group: it is told, and so is its old lead
    monkeypatch.setattr(nesting_notice, "_plan_title_of", lambda root: lambda cid: "")
    monkeypatch.setattr(nesting_notice, "_open_steps_of", lambda root: lambda lead, cid: [])
    groups.sync_membership(None, ["m"])
    assert [to for to, _ in sent] == ["m", "la"]
