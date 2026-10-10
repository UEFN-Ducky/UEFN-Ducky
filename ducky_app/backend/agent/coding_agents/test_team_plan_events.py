"""Coordinator reports follow durable plan transitions and member lifecycle."""
from types import SimpleNamespace
import ast
from pathlib import Path
import pytest
from backend.agent.coding_agents import plans, team_plan_events as events
from backend.agent.coding_agents.test_plan_assignment import team

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
    assert len(notices) == 1
    assert notices[0]["receiver_conv_id"] == "coord"
    assert "Builder" in notices[0]["body"] and notes in notices[0]["body"]
    plans.save_plan(doc, project_root=team)
    assert len(notices) == 1
    for kwargs in ({"body_markdown": "overwrite"}, {"status": "pending"}, {"assignee": "builder"}):
        with pytest.raises(ValueError):
            plans.update_node("coord", "a1", project_root=team, **kwargs)
    with pytest.raises(ValueError):
        plans.update_node("coord", "a2", content="structural change", project_root=team)
    with pytest.raises(ValueError):
        plans.add_node("coord", content="new step", project_root=team)


def test_rollup_and_whole_plan_report_once_with_notes(team, notices):
    plans.update_node("coord", "a", assignee="group-a", body_markdown="Section instructions", project_root=team)
    plans.update_node("coord", "a1", status="completed", body_markdown="Core passed", project_root=team)
    plans.update_node("coord", "a2", status="completed", body_markdown="Adapters passed", project_root=team)
    # The last step and the section it closed arrive as one notice, step first.
    assert len(notices) == 2
    last = notices[-1]["body"]
    assert "section A Modes" in last and "Section instructions" in last
    assert last.index("Adapters passed") < last.index("section A Modes")
    assert last.endswith("Dispatch the next open step at once.")
    doc = plans.update_node("coord", "b", status="completed", project_root=team)
    assert "finished plan" in notices[-1]["body"]
    assert notices[-1]["body"].endswith("check the results and report to the user.")
    assert len(notices) == 3
    assert len(doc["team_reports"]) == 4
    plans.save_plan(doc, project_root=team)
    assert len(notices) == 3


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


@pytest.mark.parametrize("reason,detail", [("done", ""), ("error", "rate_limit_error")])
def test_lifecycle_calls_once_per_run(monkeypatch, reason, detail):
    from backend.agent import a2a_broker as broker
    called = []
    monkeypatch.setattr(broker, "_retry_queue_later", lambda _: None)
    monkeypatch.setattr(broker, "_account_key", lambda _: "isolated-test")
    monkeypatch.setattr(broker, "_cooldowns", {})
    monkeypatch.setattr(broker, "_uncertain_chats", set())
    monkeypatch.setattr(events, "member_stopped", called.append)
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
    tree = ast.parse((root / "backend/tools/panel/ducky_panel.py").read_text(encoding="utf-8"))
    names = {"ducky_create_plan", "ducky_plan_add_node", "ducky_plan_update_node"}
    docs = [ast.get_docstring(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(docs) == 3
    for doc in docs:
        assert "Team plans:" in doc and "one section" in doc
        assert "preserving original notes" in doc and "at once" in doc
    skill = (root / "frontend/skill_packs/ducky/SKILL.md").read_text(encoding="utf-8")
    assert "Team plans" in skill and "two idle minutes" in skill


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
