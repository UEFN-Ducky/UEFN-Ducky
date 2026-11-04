"""Messaging another agent never kills it: a wait that runs out only stops waiting."""

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.agent.coding_agents import plans
from backend.tools.panel import ducky_panel
from frontend.ui_web.agent_turns.testing import patch_turns


def test_waiting_on_an_agent_never_cancels_it_when_the_wait_runs_out(monkeypatch) -> None:
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid, folder_id=""))
    seen: list[dict] = []

    def fake_wait(conv_id, text, mode="agent", model="", **kwargs):
        seen.append(kwargs)
        return {"status": "timeout", "conv_id": conv_id}

    patch_turns(monkeypatch, "run_message_and_wait", fake_wait)
    json.loads(ducky_panel.ducky_send_chat_message("builder", "status?", timeout_sec=5))
    assert seen and seen[0]["cancel_on_timeout"] is False


def test_messaging_a_busy_agent_without_waiting_queues_instead_of_stopping_it(monkeypatch) -> None:
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid, folder_id=""))
    seen: list[dict] = []
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: seen.append(kw) or "queued")
    out = json.loads(ducky_panel.ducky_send_chat_message("builder", "next task", wait_for_reply=False))
    assert seen and seen[0]["queue_if_busy"] is True
    assert out["status"] == "queued"


def test_a_message_from_another_ducky_says_who_sent_it(monkeypatch) -> None:
    """A lead's dispatch read as the person's own words, in the member's chat and on the hub."""
    chats = {
        "lead": SimpleNamespace(id="lead", folder_id="", title="Video Plugin Lead", coding_agent="codex"),
        "builder": SimpleNamespace(id="builder", folder_id="", title="Builder", coding_agent="codex"),
    }
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: chats.get(cid))
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: SimpleNamespace(conv_id="lead"))
    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: True)
    monkeypatch.setattr(plans, "team_plan_for", lambda cid, root=None: None)
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"chat_id": cid, "status": "open"} if cid == "lead" else None)
    sent: list[str] = []
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: sent.append(text) or "running")
    ducky_panel.ducky_send_chat_message("builder", "DISCOVERY ONLY.", wait_for_reply=False)
    lines = sent[0].splitlines()
    assert lines[0] == "[ducky:agent-message] from Video Plugin Lead (chat lead) [codex]"
    assert "assignment from your group leader" in lines[1]
    assert sent[0].endswith("\n\nDISCOVERY ONLY.")
    # The person (no agent context) still sends plain words.
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: None)
    ducky_panel.ducky_send_chat_message("builder", "hi", wait_for_reply=False)
    assert sent[1] == "hi"


def test_a_lead_with_no_plan_hands_out_no_work_by_message(monkeypatch) -> None:
    # Oct 15 2026: a lead handed out work by message before writing any plan; a member then wrote
    # a plan of its own and the keeper woke that member as a lead.
    chats = {
        "lead": SimpleNamespace(id="lead", folder_id="", title="Lead", coding_agent="codex"),
        "builder": SimpleNamespace(id="builder", folder_id="", title="Builder", coding_agent="codex"),
    }
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: chats.get(cid))
    me = {"id": "lead"}
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: SimpleNamespace(conv_id=me["id"]))
    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: sender == "lead")
    monkeypatch.setattr(plans, "team_plan_for", lambda cid, root=None: None)
    lead_plan: dict = {"doc": None}
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: lead_plan["doc"] if cid == "lead" else None)
    sent: list[str] = []
    patch_turns(monkeypatch, "run_message", lambda cid, text, mode, model, **kw: sent.append(cid) or "running")
    out = json.loads(ducky_panel.ducky_send_chat_message("builder", "Build the CLI.", wait_for_reply=False))
    assert out["status"] == "not_sent" and "ducky_create_plan" in out["reason"] and sent == []
    lead_plan["doc"] = {"chat_id": "lead", "status": "finished"}  # a finished plan holds no new work
    assert json.loads(ducky_panel.ducky_send_chat_message("builder", "One more.", wait_for_reply=False))["status"] == "not_sent"
    lead_plan["doc"] = {"chat_id": "lead", "status": "open"}
    ducky_panel.ducky_send_chat_message("builder", "Which port did you pick?", wait_for_reply=False)
    assert sent == ["builder"]
    # A member writing to its lead is not a lead handing out work.
    me["id"], lead_plan["doc"] = "builder", None
    ducky_panel.ducky_send_chat_message("lead", "Port 8080.", wait_for_reply=False)
    assert sent == ["builder", "lead"]


def test_a_lead_whose_team_has_members_spawns_no_work_before_its_plan(monkeypatch) -> None:
    hub = SimpleNamespace(id="hub", is_group=True, title="Ledger Team", folder_id="f1", leader_conv_id="lead")
    lead = SimpleNamespace(id="lead", is_group=False, title="Lead", parent_conv_id="", folder_id="", coding_agent="codex")
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: {"hub": hub, "lead": lead}.get(cid))
    monkeypatch.setattr(ducky_panel, "_resolve_ducky_profile", lambda ducky: {"id": "verse-coder", "name": "Verse Coder"})
    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: False)
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: None)
    staffed: dict = {"leads": set()}
    monkeypatch.setattr("frontend.ui_web.team_keeper._staffed_leaders", lambda root: staffed["leads"])
    seated: list[str] = []

    class Api:
        def group_seat_profile(self, group_id, profile_id, **kw):
            seated.append(group_id)
            return {"ok": True, "member": {"member_conv_id": f"m{len(seated)}", "name": "Verse Coder"}}

    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: Api())
    patch_turns(monkeypatch, "run_message", lambda *a, **k: "running")
    monkeypatch.setattr("backend.agent.a2a_client.open_thread", lambda *a, **k: "rid")

    def spawn(text: str) -> dict:
        return json.loads(ducky_panel.ducky_spawn_chat(text, ducky="Verse Coder", group_id="hub", sender="lead",
                                                       wait_for_reply=False))

    # A group with no members yet: an ordinary helper spawn.
    assert spawn("Build the models.")["status"] == "running" and seated == ["hub"]
    # Its team has a member now: the next work goes in the plan, and nobody is seated.
    staffed["leads"] = {"lead"}
    out = spawn("Build the CLI.")
    assert out["status"] == "not_sent" and "ducky_group_invite" in out["reason"] and seated == ["hub"]
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"chat_id": cid, "status": "open"})
    assert spawn("Build the CLI.")["status"] == "running" and seated == ["hub", "hub"]
    # Nov 3 2026: a lead whose plan was finished (all 17 steps done) was told it had none yet.
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"chat_id": cid, "status": "finished"})
    assert spawn("Help with one more thing.")["status"] == "running" and seated == ["hub", "hub", "hub"]


def test_the_chat_that_spawns_into_a_leaderless_group_leads_it_and_invite_finds_the_group(monkeypatch) -> None:
    # Nov 3 2026: after spawning into a group by id, ducky_group_invite answered "Not a group chat"
    # (given the member's chat id) and "This group has no leader yet".
    hub = SimpleNamespace(id="hub", is_group=True, title="Meshy", folder_id="f1", leader_conv_id="")
    lead = SimpleNamespace(id="lead", is_group=False, title="Lead", parent_conv_id="", folder_id="", coding_agent="codex")
    member = SimpleNamespace(id="m1", is_group=False, title="Dev", parent_conv_id="hub", folder_id="f1",
                             model="", provider="", coding_agent="claude_code")
    chats = {"hub": hub, "lead": lead, "m1": member}
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: chats.get(cid))
    monkeypatch.setattr(ducky_panel, "save_conversation", lambda conv, root=None: None)
    monkeypatch.setattr(ducky_panel, "load_folders", lambda root=None: [SimpleNamespace(id="f1", group_hub_id="hub")])
    monkeypatch.setattr(ducky_panel, "_resolve_ducky_profile", lambda ducky: {"id": "developer", "name": "Developer"})
    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: False)
    monkeypatch.setattr("frontend.ui_web.team_keeper._staffed_leaders", lambda root: set())
    monkeypatch.setattr("backend.workspace.identity.resolve_context", lambda: SimpleNamespace(conv_id="lead"))
    invited: list[str] = []

    class Api:
        def group_seat_profile(self, group_id, profile_id, **kw):
            return {"ok": True, "member": {"member_conv_id": "m1", "name": "Dev"}}

        def group_invite(self, group_id, profile_id, **kw):
            invited.append(group_id)
            return {"ok": True, "member": {"member_conv_id": "m2"}}

        def group_lane_authorities(self, group_id):
            return [chats[group_id].leader_conv_id] if chats[group_id].leader_conv_id else []

    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: Api())
    patch_turns(monkeypatch, "run_message", lambda *a, **k: "running")
    monkeypatch.setattr("backend.agent.a2a_client.open_thread", lambda *a, **k: "rid")
    ducky_panel.ducky_spawn_chat("Build it.", ducky="Developer", group_id="hub", sender="lead", wait_for_reply=False)
    assert hub.leader_conv_id == "lead"
    for target in ("m1", "f1", "hub"):  # the member's chat, the group's folder, the group
        json.loads(ducky_panel.ducky_group_invite(target, "Developer", write_allowed=["src/**"]))
    assert invited == ["hub", "hub", "hub"]
