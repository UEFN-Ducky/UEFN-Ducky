"""Coordinator reports follow durable plan transitions and member lifecycle."""
from types import SimpleNamespace
import ast
from pathlib import Path
import pytest
from backend.agent.coding_agents import plans, team_plan_events as events
from backend.agent.coding_agents.test_plan_assignment import team  # noqa: F401  (fixture)

@pytest.fixture
def notices(monkeypatch):
    from backend.agent import a2a_client
    from backend.workspace import identity
    messages = []
    monkeypatch.setattr(a2a_client, "send_notice", lambda **kw: messages.append(kw))
    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="builder", ducky_name="Builder"))
    return messages

@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_notes_and_tick_while_playing_freeze_finished_nodes(team, notices, status):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "a1", body_markdown="Original instructions", project_root=team)
    notes = "Original instructions\nResult: " + status + "; dependency unavailable"
    doc = plans.update_node("coord", "a1", status=status, body_markdown=notes, project_root=team)
    assert plans._walk_find(doc["nodes"], "a1")[2]["body_markdown"] == notes
    # A finished point of a sub plan is not news for the lead until the sub plan is done;
    # a cancelled one is (its reason may need the lead).
    told = 1 if status == "cancelled" else 0
    assert len(notices) == told
    if told:
        assert notices[0]["receiver_conv_id"] == "coord"
        assert "Builder" in notices[0]["body"] and notes in notices[0]["body"]
    plans.save_plan(doc, project_root=team)
    assert len(notices) == told
    for kwargs in ({"body_markdown": "overwrite"}, {"status": "pending"}, {"assignee": "builder"}):
        with pytest.raises(ValueError):
            plans.update_node("coord", "a1", project_root=team, **kwargs)
    # An unstarted step is reworded while the plan plays.
    doc = plans.update_node("coord", "a2", content="structural change", project_root=team)
    assert plans._walk_find(doc["nodes"], "a2")[2]["content"] == "structural change"
    with pytest.raises(ValueError):
        plans.add_node("coord", content="under a finished step", parent_id="a1", project_root=team)
    plans.add_node("coord", content="new step", project_root=team)  # adding needs no pause


def test_rollup_and_whole_plan_report_once_with_notes(team, notices):
    plans.update_node("coord", "a", assignee="group-a", body_markdown="Section instructions", project_root=team)
    plans.update_node("coord", "a1", status="completed", body_markdown="Core passed", project_root=team)
    # The lead hears once, when the sub plan is done: its last point and the sub plan together.
    assert len(notices) == 0
    plans.update_node("coord", "a2", status="completed", body_markdown="Adapters passed", project_root=team)
    assert len(notices) == 1
    last = notices[-1]["body"]
    assert "section A Modes" in last and "Section instructions" in last
    assert last.index("Adapters passed") < last.index("section A Modes")
    assert last.endswith("change the plan only if this result needs it.")
    doc = plans.update_node("coord", "b", status="completed", project_root=team)
    assert "finished plan" in notices[-1]["body"]
    assert notices[-1]["body"].endswith("check the results and report to the user.")
    assert len(notices) == 2
    assert len(doc["team_reports"]) == 3
    plans.save_plan(doc, project_root=team)
    assert len(notices) == 2


def test_new_dispatch_acknowledges_retained_reports(team, notices):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    plans.update_node("coord", "a1", status="completed", project_root=team)
    doc = plans.update_node("coord", "a2", status="in_progress", project_root=team)
    assert doc["team_reports"] == []


def test_stopped_member_reports_only_own_coordinator(team, notices, monkeypatch):
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    doc = plans.load_plan("coord", project_root=team)
    monkeypatch.setattr(plans, "list_plans", lambda: [doc])
    events.member_stopped("builder")
    assert len(notices) == 1
    assert notices[0]["receiver_conv_id"] == "coord"
    assert "builder stopped without finishing Core (a1)" in notices[0]["body"]
    events.member_stopped("outsider")
    assert len(notices) == 1
    events.member_stopped("builder", "done")  # a turn that simply ended: the keeper restarts it
    assert len(notices) == 1


def test_a_stopped_turn_tells_the_seat_queue(monkeypatch):
    from frontend.ui_web import seat_queue

    ended = []
    monkeypatch.setattr(seat_queue, "on_turn_end", lambda *args: ended.append(args))
    monkeypatch.setattr(plans, "list_plans", lambda: [])
    events.member_stopped("seat-1", "error", "Ducky's tools didn't connect")
    assert ended == [("seat-1", "error", "Ducky's tools didn't connect")]
    events.member_stopped("seat-1", "done", "All 3 steps done.")  # a clean stop: no error text
    assert ended[1:] == [("seat-1", "done", "")]


def test_a_broken_seat_queue_never_breaks_the_stop(monkeypatch):
    from frontend.ui_web import seat_queue

    def boom(*args):
        raise RuntimeError("queue gone")
    monkeypatch.setattr(seat_queue, "on_turn_end", boom)
    monkeypatch.setattr(plans, "list_plans", lambda: [])
    events.member_stopped("seat-1", "error", "boom")


@pytest.mark.parametrize("reason,detail", [("done", ""), ("error", "rate_limit_error")])
def test_lifecycle_calls_once_per_run(monkeypatch, reason, detail):
    from backend.agent import a2a_broker as broker
    called = []
    monkeypatch.setattr(broker, "_retry_queue_later", lambda _: None)
    monkeypatch.setattr(broker, "_account_key", lambda _: "isolated-test")
    monkeypatch.setattr(broker, "_cooldowns", {})
    monkeypatch.setattr(broker, "_uncertain_chats", set())
    monkeypatch.setattr(events, "member_stopped", lambda conv_id, *rest: called.append(conv_id))
    monkeypatch.setattr(broker, "_completed_runs", set())
    monkeypatch.setattr(broker, "open_threads_for_receiver", lambda _: [])
    monkeypatch.setattr(broker, "_schedule_reports", lambda _: None)
    monkeypatch.setattr(broker, "_kick_delivery", lambda _: None)
    monkeypatch.setattr(broker, "_reports", {})
    broker.on_agent_stopped("builder", reason, detail=detail, run_id="unique-plan-run")
    broker.on_agent_stopped("builder", reason, detail=detail, run_id="unique-plan-run")
    assert called == ["builder"]


def test_team_plan_tool_and_skill_documentation():
    root = Path(__file__).resolve().parents[3]
    names = {"ducky_create_plan", "ducky_plan_add_node", "ducky_plan_update_node"}
    docs = [
        ast.get_docstring(n)
        for module in ("ducky_panel.py", "ducky_panel_plans.py")
        for n in ast.parse((root / "backend/tools/panel" / module).read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    assert len(docs) == 3
    for doc in docs:
        assert "Team plans:" in doc and "one sub plan per agent" in doc
        assert "preserving original notes" in doc and "Nobody dispatches by message" in doc
    skill = (root / "frontend/skill_packs/ducky/SKILL.md").read_text(encoding="utf-8")
    assert "Team plans" in skill and "two idle minutes" in skill and "BLOCKED:" in skill


def test_members_on_a_team_plan_send_no_messages(monkeypatch):
    # Oct 12 2026 owner: agents talked in circles instead of coding; a team works like a main
    # agent with sub-agents, which send nothing but their result.
    from backend.tools.panel import ducky_panel as panel

    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: sender == "lead")
    monkeypatch.setattr(plans, "team_plan_for",
                        lambda cid, root=None: {"chat_id": "lead"} if cid == "builder" else None)
    assert panel._works_alone("builder", "lead", None)
    assert panel._works_alone("builder", "peer", None)
    assert not panel._works_alone("lead", "builder", None)
    assert not panel._works_alone("solo", "other", None)
    assert "BLOCKED:" in panel.WORK_ALONE


@pytest.mark.parametrize("database", [False, True])
def test_acknowledgement_after_durable_save(team, notices, monkeypatch, database):
    from backend.agent import a2a_client
    from backend.workspace import identity
    import copy
    previous = plans.load_plan("coord", project_root=team)
    saved = []
    if database:
        monkeypatch.setattr(plans, "_use_db", lambda: True)
        monkeypatch.setattr(plans, "_repo", lambda _: SimpleNamespace(
            plan_get=lambda *args: copy.deepcopy(previous),
            plan_put=lambda *args: saved.append(copy.deepcopy(args[-1]))))
    else:
        original_write = plans.write_json_atomic
        def write(path, doc):
            original_write(path, doc)
            saved.append(copy.deepcopy(doc))
        monkeypatch.setattr(plans, "write_json_atomic", write)
    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="coord"))
    acknowledged = []
    def ack(operation, before, after, actor, root):
        assert operation == "acknowledge_plan_changes"
        assert saved and saved[-1] == after
        assert before["nodes"] == previous["nodes"]
        acknowledged.append((actor, root))
    monkeypatch.setattr(a2a_client, "_call", ack)
    plans.update_node("coord", "a1", body_markdown="Coordinator acted", project_root=team)
    assert acknowledged == [("coord", team)]
    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="builder"))
    plans.update_node("coord", "a1", body_markdown="Member acted", project_root=team)
    assert len(acknowledged) == 1


def test_failed_save_does_not_acknowledge(team, notices, monkeypatch):
    from backend.agent import a2a_client
    from backend.workspace import identity
    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="coord"))
    acknowledged = []
    monkeypatch.setattr(a2a_client, "acknowledge_plan_changes", lambda *args: acknowledged.append(args))
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(plans, "write_json_atomic", fail)
    with pytest.raises(OSError):
        plans.update_node("coord", "a1", body_markdown="unsaved", project_root=team)
    assert acknowledged == []


def test_report_retries_stop_once_the_members_steps_are_finished(team, notices, monkeypatch):
    from backend.agent import a2a_broker

    load = plans.load_plan
    monkeypatch.setattr(plans, "load_plan", lambda cid, project_root=None: load(cid, project_root=team))
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    assert a2a_broker._plan_work_done("coord", "builder") is False  # a1 still in progress
    plans.update_node("coord", "a1", status="completed", project_root=team)
    plans.update_node("coord", "a2", status="cancelled", body_markdown="not needed", project_root=team)
    assert a2a_broker._plan_work_done("coord", "builder") is True
    assert a2a_broker._plan_work_done("coord", "outsider") is False  # owns nothing in this plan


def test_the_last_tick_says_the_section_closed_itself_and_its_note_is_refused_clearly(team, notices, monkeypatch):
    # Oct 10 2026: writers finished their step, then tried to annotate the section that had
    # already closed itself and got a bare refusal: one wasted call each.
    import json as _json

    from backend.tools.panel import ducky_panel

    monkeypatch.setattr(ducky_panel, "_project_root", lambda: team)
    plans.update_node("coord", "a", assignee="group-a", project_root=team)
    first = _json.loads(ducky_panel.ducky_plan_update_node(node_id="a1", status="completed", chat_id="coord"))
    assert "note" not in first
    last = _json.loads(ducky_panel.ducky_plan_update_node(node_id="a2", status="completed", chat_id="coord"))
    assert last["sections_completed"] == ["a"]
    assert "completed itself with this step" in last["note"]
    refused = _json.loads(ducky_panel.ducky_plan_update_node(node_id="a", body_markdown="Done.", chat_id="coord"))
    assert refused["ok"] is False and "completed itself when its last step did" in refused["error"]


def test_a_lead_changes_the_plan_instead_of_messaging_a_working_member(monkeypatch):
    from backend.tools.panel import ducky_panel as panel

    monkeypatch.setattr("backend.agent.a2a_broker.leads_receiver", lambda sender, receiver: sender == "lead")
    monkeypatch.setattr(plans, "team_plan_for",
                        lambda cid, root=None: {"chat_id": "lead"} if cid == "builder" else None)
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"chat_id": cid} if cid == "lead" else None)
    assert panel._lead_uses_plan("lead", "builder", None)
    assert not panel._lead_uses_plan("lead", "idle-member", None)  # no plan work: a message is fine
    assert not panel._lead_uses_plan("builder", "lead", None)
    assert "ducky_plan_add_node" in panel.LEAD_USES_PLAN


def test_an_inner_sub_plan_done_is_kept_on_the_plan_not_a_lead_turn(notices):
    # Oct 15 2026: a nested team (a sub plan per group, inside it one per member) woke its lead
    # seven times; four were members' inner sub plans finishing, where Ducky already starts
    # whatever waited on them and the lead had nothing to do.
    def ledger(models, integrate):
        core = "completed" if models == integrate == "completed" else "in_progress"
        return {"chat_id": "coord", "title": "Ledger", "status": "finished" if core == "completed" else "open",
                "nodes": [{"id": "core", "content": "Core", "assignee": "group-core", "status": core, "children": [
                    {"id": "models", "content": "Models", "assignee": "builder", "status": models, "children": [
                        {"id": "m1", "content": "Transaction", "status": models}]},
                    {"id": "integrate", "content": "Integrate", "assignee": "core-lead", "status": integrate}]}]}

    first = events.completion_reports(ledger("pending", "pending"), ledger("completed", "pending"))
    assert first and all(r.get("quiet") for r in first)  # kept on the plan
    events.deliver_reports(ledger("completed", "pending"), first)
    assert notices == []  # but no lead turn
    last = events.completion_reports(ledger("completed", "pending"), ledger("completed", "completed"))
    assert not any(r.get("quiet") for r in last)
    events.deliver_reports(ledger("completed", "completed"), last)
    assert len(notices) == 1
    body = notices[0]["body"]
    assert "step Integrate" in body and "section Core" in body and "finished plan Ledger" in body


def test_reports_carry_progress_computed_from_the_plan(notices):
    titles = ["Enforce", "Move rules", "Prompt rules", "Core tools", "Descriptions"]

    def doc(statuses, section="in_progress"):
        steps = [{"id": f"s{i}", "content": t, "status": s} for i, (t, s) in enumerate(zip(titles, statuses))]
        return {"chat_id": "coord", "title": "Tools", "status": "open",
                "nodes": [{"id": "rules", "content": "Rules", "assignee": "builder", "status": section, "children": steps}]}

    # Mid-plan: a cancelled point is news for the lead, with how far the sub plan is.
    (mid,) = events.completion_reports(doc(["completed", "pending", "pending", "pending", "pending"]),
                                       doc(["completed", "cancelled", "pending", "pending", "pending"]))
    assert "Progress: 2/5 steps (40%) · next: Prompt rules" in mid["body"]
    # Final: the last point closes the sub plan; the section report says it is all done.
    last = events.completion_reports(doc(["completed"] * 4 + ["pending"]), doc(["completed"] * 5, "completed"))
    assert [r["body"].split("\n")[1] for r in last if "section Rules" in r["body"]] == ["All 5 steps done"]


def test_plan_edit_tools_return_the_node_not_the_whole_plan(team, notices, monkeypatch):
    # Oct 23 2026: add_node returned the whole 63 KB plan with every note on each edit.
    import json as _json

    from backend.tools.panel import ducky_panel

    monkeypatch.setattr(ducky_panel, "_project_root", lambda: team)
    for i in range(30):
        plans.add_node("coord", content=f"Step {i}", body_markdown="Long notes. " * 150, project_root=team)
    assert len(_json.dumps(plans.load_plan("coord", project_root=team))) > 50_000
    added = ducky_panel.ducky_plan_add_node(content="One more step", body_markdown="Do it.", chat_id="coord")
    assert len(added) < 6_000
    out = _json.loads(added)
    assert out["ok"] and out["node"]["content"] == "One more step" and out["node"]["body_markdown"] == "Do it."
    assert "nodes" not in out["plan"] and out["progress"]["total"] >= 31
    assert "outline" not in out  # ducky_get_plan(outline=true) reads it
    node_id = out["node"]["id"]
    assert len(ducky_panel.ducky_plan_move_node(node_id=node_id, index=0, chat_id="coord")) < 6_000
    deleted = _json.loads(ducky_panel.ducky_plan_delete_node(node_id=node_id, chat_id="coord"))
    if deleted["ok"]:
        assert "nodes" not in deleted["plan"] and deleted["node"] == {"id": node_id, "deleted": True}


def test_the_leads_own_change_does_not_wake_the_lead(notices, monkeypatch):
    # Oct 22 2026: an Opus lead cancelled a step after its report and was woken by a notice about
    # its own cancel, to answer "nothing has changed since". A tick in the plan view still goes.
    from backend.workspace import identity

    def doc(status):
        return {"chat_id": "coord", "title": "Streak", "status": "open",
                "nodes": [{"id": "hud", "content": "HUD", "assignee": "member", "status": "in_progress", "children": [
                    {"id": "verify", "content": "Verify", "status": status}, {"id": "draw", "content": "Draw", "status": "pending"}]}]}

    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="coord", ducky_name="Lead"))
    reports = events.completion_reports(doc("pending"), doc("cancelled"))
    assert reports and all(r.get("quiet") for r in reports)
    events.deliver_reports(doc("cancelled"), reports)
    assert notices == []
    monkeypatch.setattr(identity, "resolve_context", lambda: None)  # the person, in the plan view
    reports = events.completion_reports(doc("pending"), doc("cancelled"))
    events.deliver_reports(doc("cancelled"), reports)
    assert len(notices) == 1 and "cancelled step Verify" in notices[0]["body"]


def test_a_leads_reassign_notes_or_new_step_nudge_the_member_but_its_own_notes_dont(team, notices, monkeypatch):
    # Plan 14283125831b: re-setting the same assignee and rewriting an in_progress point were no
    # change at all, so the stopped member never started. The keeper reads ``nudged_at``.
    from backend.workspace import identity

    def nudged(doc, node_id):
        return plans._walk_find(doc["nodes"], node_id)[2].get("nudged_at")

    plans.update_node("coord", "a", assignee="builder", project_root=team)  # notices fixture: the member calls
    plans.update_node("coord", "a1", status="in_progress", body_markdown="my notes", project_root=team)
    doc = plans.load_plan("coord", project_root=team)
    assert nudged(doc, "a") is None and nudged(doc, "a1") is None
    monkeypatch.setattr(identity, "resolve_context", lambda: SimpleNamespace(conv_id="coord", ducky_name="Lead"))
    doc = plans.update_node("coord", "a", assignee="builder", project_root=team)  # same owner again
    first = nudged(doc, "a")
    assert first
    doc = plans.update_node("coord", "a1", body_markdown="Retry: the API is back.", project_root=team)
    assert nudged(doc, "a1") and nudged(plans.load_plan("coord", project_root=team), "a") == first  # it persists
    doc = plans.add_node("coord", content="One more", parent_id="a", project_root=team)
    assert nudged(doc, doc["nodes"][0]["children"][-1]["id"])
    monkeypatch.setattr(identity, "resolve_context", lambda: None)  # the person, in the plan view
    assert nudged(plans.update_node("coord", "a2", body_markdown="Use v2.", project_root=team), "a2")
