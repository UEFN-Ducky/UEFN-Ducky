"""The daily island check: Repeat until, Fortnite servers up?, Message me, an Agent that
stays the same ducky across tries, schedules that run in the background, and the UEFN
plugin's 8 AM template run end to end (UEFN, the duckies and Epic's status page faked)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from backend.automations import catalog, runner, scheduler, servers, store
from backend.automations.pins import check_wires

PLUGIN_JSON = Path(__file__).resolve().parents[4] / "uefn-plugins" / "uefn-plugin-uefn" / "plugin.json"


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "use_db", lambda *_: False)
    monkeypatch.setattr(store, "_files_dir", lambda: tmp_path / "wf")
    (tmp_path / "wf").mkdir()
    monkeypatch.setattr(store, "_announce_graphs_changed", lambda: None)
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", lambda _e: None)
    monkeypatch.setattr("frontend.ui_web.agent_modes.notify_chats_changed", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_ensure_pipeline_group", lambda *a: None)  # no agent group folder in tests


def save(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], name: str = "Daily", **extra: Any) -> str:
    return store.save_workflow({"name": name, "graph": {"nodes": nodes, "edges": edges}, **extra})["id"]


def node(nid: str, ntype: str, label: str = "", inputs: dict[str, Any] | None = None, **config: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": nid, "type": ntype, "x": 0, "y": 0, "config": dict(config)}
    if inputs:
        row["config"]["inputs"] = inputs
    if label:
        row["label"] = label
    return row


def go(a: str, b: str, kind: str = "main") -> dict[str, Any]:
    return {"source": a, "target": b, "kind": kind}


def wire(a: str, ap: str, b: str, bp: str) -> dict[str, Any]:
    return {"source": a, "target": b, "kind": "data", "source_pin": ap, "target_pin": bp}


def ran(out: dict[str, Any], nid: str) -> int:
    return sum(1 for step in out["steps"] if step.get("id") == nid)


def status_page(**parts: str) -> dict[str, Any]:
    """Epic's summary.json: the Fortnite group and its parts (all up unless named)."""
    comps: list[dict[str, Any]] = [{"id": "fn", "name": "Fortnite", "group": True, "group_id": None, "status": "operational"}]
    for name in ("Login", "Game Services", "Matchmaking", "Item Shop"):
        comps.append({"id": name.lower(), "name": name, "group": False, "group_id": "fn", "status": parts.get(name, "operational")})
    return {"components": comps, "scheduled_maintenances": []}


class Clock:
    """Fake time for the servers wait: sleeping moves the clock, nothing really waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------- Fortnite servers up?


def test_fortnite_status_reads_the_parts_a_play_session_needs():
    assert servers.fortnite_status(status_page()) == {"up": True, "status": "Up", "down": []}
    assert servers.fortnite_status(status_page(**{"Item Shop": "major_outage"}))["up"] is True  # doesn't stop a test
    down = servers.fortnite_status(status_page(Login="major_outage"))
    assert down["up"] is False and down["status"] == "Down — Login: major outage"
    slow = servers.fortnite_status(status_page(Matchmaking="degraded_performance"))
    assert slow["up"] is True and slow["status"] == "Up (slow: Matchmaking)"
    page = status_page()
    page["scheduled_maintenances"] = [{"name": "v42.20 update", "status": "in_progress", "components": [{"id": "login", "group_id": "fn"}]},
                                      {"name": "Old window", "status": "completed", "components": [{"id": "login", "group_id": "fn"}]}]
    assert servers.fortnite_status(page)["status"] == "Down — Maintenance: v42.20 update"
    assert servers.fortnite_status({"components": []})["up"] is False


def test_servers_check_waits_out_downtime_then_takes_true():
    pages = [status_page(Login="under_maintenance"), status_page(Login="under_maintenance"), status_page()]
    clock = Clock()
    out = servers.wait_for_servers({"wait_minutes": 30, "every_seconds": 60}, fetch=lambda: pages.pop(0), sleep=clock.sleep, clock=clock)
    assert out["branch"] is True and out["outputs"] == {"up": True, "status": "Up"}
    assert out["result"]["checks"] == 3 and clock.now == pytest.approx(120)


def test_servers_check_takes_false_when_still_down_or_unreachable():
    clock = Clock()
    out = servers.wait_for_servers({"wait_minutes": 2, "every_seconds": 60}, fetch=lambda: status_page(Login="major_outage"), sleep=clock.sleep, clock=clock)
    assert out["branch"] is False and out["result"]["checks"] == 3 and out["result"]["servers_status"].startswith("Down")

    def offline() -> dict[str, Any]:
        raise OSError("no network")

    out = servers.wait_for_servers({"wait_minutes": 0}, fetch=offline, sleep=clock.sleep, clock=clock)
    assert out["branch"] is False and "Couldn't reach the Epic status page" in out["outputs"]["status"]
    stopped = servers.wait_for_servers({"wait_minutes": 240}, fetch=offline, sleep=clock.sleep, clock=clock, cancelled=lambda: True)
    assert stopped["result"]["checks"] == 1  # Stop ends the wait at once
    with pytest.raises(ValueError, match="numbers"):
        servers.wait_for_servers({"wait_minutes": "soon"}, fetch=offline)


def test_fortnite_servers_node_leads_true_or_false(monkeypatch):
    pages = [status_page(), status_page(**{"Game Services": "major_outage"})]
    monkeypatch.setattr(servers, "fetch_summary", lambda timeout=15.0: pages.pop(0))
    wid = save(
        [node("s", "start.manual"), node("up", "fortnite.servers", wait_minutes=0),
         node("yes", "flow.wait", seconds=0), node("no", "flow.wait", seconds=0)],
        [go("s", "up"), go("up", "yes", "true"), go("up", "no", "false")],
    )
    first = runner.run_workflow(wid)
    assert first["ok"] and ran(first, "yes") == 1 and ran(first, "no") == 0
    assert first["node_outputs"]["up"] == {"up": True, "status": "Up"}
    second = runner.run_workflow(wid)
    assert ran(second, "yes") == 0 and ran(second, "no") == 1


# --------------------------------------------------------------------------- Repeat until


def _tool_replies(monkeypatch, replies: list[Any], on_call=None) -> list[int]:
    calls: list[int] = []

    def call(cfg: dict[str, Any], payload: dict[str, Any], node_id: str = "") -> dict[str, Any]:
        calls.append(payload.get("attempt"))
        if on_call:
            on_call(len(calls))
        reply = replies[min(len(calls), len(replies)) - 1]
        return reply if isinstance(reply, dict) else {"ok": True, "result": {"text": reply}}

    monkeypatch.setattr(runner, "_call_tool", call)
    return calls


def _loop_graph(max_tries: Any = 5, **repeat: Any) -> str:
    """Repeat → [tool → template (a data node) → If PASS] until the If says yes; done → wait."""
    return save(
        [node("s", "start.manual"), node("r", "flow.repeat", max=max_tries, **repeat),
         node("t", "tool.call", name="session_status"),
         node("x", "text.template", template="seen: {{a}}", names=["a"]),
         node("i", "logic.if", expression='report.includes("PASS")', names=["report"]),
         node("after", "flow.wait", seconds=0)],
        [go("s", "r"), go("r", "t", "each"), go("t", "i"), go("r", "after", "done"),
         wire("t", "text", "x", "a"), wire("x", "text", "i", "report"), wire("i", "result", "r", "until")],
    )


def test_repeat_until_goes_again_until_the_check_says_yes(monkeypatch):
    calls = _tool_replies(monkeypatch, ["FAIL", "FAIL", "PASS", "PASS"])
    out = runner.run_workflow(_loop_graph())
    assert out["ok"], out["error"]
    assert calls == [1, 2, 3]  # each try knows its number
    assert ran(out, "t") == 3 and ran(out, "after") == 1
    assert out["node_outputs"]["r"] == {"attempt": 3, "passed": True}
    assert out["node_outputs"]["x"] == {"text": "seen: PASS"}  # the data node was worked out again each try


def test_repeat_until_gives_up_after_max_tries_and_still_follows_done(monkeypatch):
    calls = _tool_replies(monkeypatch, ["FAIL"])
    out = runner.run_workflow(_loop_graph(max_tries=2))
    assert out["ok"] and len(calls) == 2 and ran(out, "after") == 1
    assert out["node_outputs"]["r"] == {"attempt": 2, "passed": False}


def test_repeat_until_counts_a_failed_try_and_goes_on(monkeypatch):
    calls = _tool_replies(monkeypatch, [{"ok": False, "error": "UEFN is busy"}, "PASS"])
    out = runner.run_workflow(_loop_graph())
    assert out["ok"] and len(calls) == 2
    assert out["node_outputs"]["r"] == {"attempt": 2, "passed": True}
    repeat = next(step for step in out["steps"] if step.get("id") == "r")
    assert "last_error" not in repeat["result"]

    calls = _tool_replies(monkeypatch, [{"ok": False, "error": "UEFN is busy"}])
    out = runner.run_workflow(_loop_graph(max_tries=2))
    repeat = next(step for step in out["steps"] if step.get("id") == "r")
    assert out["ok"] and repeat["result"]["last_error"] == "UEFN is busy" and repeat["result"]["passed"] is False


def test_stop_ends_a_repeat_at_once(monkeypatch):
    wid = _loop_graph()
    calls = _tool_replies(monkeypatch, ["FAIL"], on_call=lambda _n: runner.stop_workflow(wid))
    out = runner.run_workflow(wid)
    assert out["ok"] is False and out["error"] == runner.STOPPED and len(calls) == 1


def test_repeat_n_times_or_until_a_condition(monkeypatch):
    calls = _tool_replies(monkeypatch, ["anything"])
    plain = save([node("s", "start.manual"), node("r", "flow.repeat", max=3), node("t", "tool.call", name="x")],
                 [go("s", "r"), go("r", "t", "each")])
    out = runner.run_workflow(plain)
    assert out["ok"] and len(calls) == 3 and out["node_outputs"]["r"]["passed"] is False  # nothing to check: every try runs

    calls.clear()
    cond = save([node("s", "start.manual"), node("r", "flow.repeat", max=9, expression="attempt >= 2"), node("t", "tool.call", name="x")],
                [go("s", "r"), go("r", "t", "each")])
    out = runner.run_workflow(cond)
    assert len(calls) == 2 and out["node_outputs"]["r"] == {"attempt": 2, "passed": True}

    calls.clear()
    capped = save([node("s", "start.manual"), node("r", "flow.repeat", max=50), node("t", "tool.call", name="x")],
                  [go("s", "r"), go("r", "t", "each")])
    assert runner.run_workflow(capped)["ok"] and len(calls) == runner._REPEAT_CAP

    bad = save([node("s", "start.manual"), node("r", "flow.repeat", max="lots")], [go("s", "r")])
    out = runner.run_workflow(bad)
    assert out["ok"] is False and "Max tries must be a number" in out["error"]
    bad_until = save([node("s", "start.manual"), node("r", "flow.repeat", max=2, expression="attempt >"), node("t", "tool.call", name="x")],
                     [go("s", "r"), go("r", "t", "each")])
    assert runner.run_workflow(bad_until)["error"].startswith("Until:")
    alone = runner._exec_node(node("r", "flow.repeat", max=4), {}, {})  # Run this node
    assert alone["ok"] and alone["result"] == {"max_tries": 4}


# --------------------------------------------------------------------------- Agent in a loop


def _fake_duckies(monkeypatch, tmp_path, replies) -> tuple[list[str], list[tuple[str, str]]]:
    made: list[str] = []
    sent: list[tuple[str, str]] = []

    def create(cfg, payload):
        made.append(f"conv{len(made) + 1}")
        return {"ok": True, "conv_id": made[-1]}

    def wait(conv_id, prompt, mode, model, **kwargs):
        sent.append((conv_id, prompt))
        return replies(conv_id, prompt, len(sent))

    monkeypatch.setattr(runner, "_create_pipeline_ducky", create)
    monkeypatch.setattr(runner, "_run_message_and_wait", wait)
    monkeypatch.setattr("backend.automations.artifacts.chat_dir", lambda conv_id: tmp_path / "chats" / conv_id)
    return made, sent


def test_an_agent_in_a_loop_stays_the_same_ducky_and_gets_the_wired_context(monkeypatch, tmp_path):
    made, sent = _fake_duckies(monkeypatch, tmp_path, lambda conv, prompt, n: {"status": "done", "assistant_text": f"report {n}"})
    wid = save(
        [node("s", "start.manual"), node("r", "flow.repeat", max=3, expression="attempt >= 3"),
         node("tester", "pipeline.agent", ducky="__new__", prompt="Test {{workflow_name}}, try {{attempt}}"),
         node("fixer", "pipeline.agent", ducky="__new__", prompt="Fix these:")],
        [go("s", "r"), go("r", "tester", "each"), go("tester", "fixer"), wire("tester", "text", "fixer", "context")],
        name="Island",
    )
    out = runner.run_workflow(wid)
    assert out["ok"], out["error"]
    assert made == ["conv1", "conv2"]  # one tester, one fixer, for all three tries
    assert [p for c, p in sent if c == "conv1"] == ["Test Island, try 1", "Test Island, try 2", "Test Island, try 3"]
    assert [p for c, p in sent if c == "conv2"] == ["Fix these:\n\nreport 1", "Fix these:\n\nreport 3", "Fix these:\n\nreport 5"]
    assert out["node_outputs"]["fixer"]["text"] == "report 6"


def test_an_agent_that_failed_gets_a_fresh_ducky_next_try(monkeypatch, tmp_path):
    made, sent = _fake_duckies(monkeypatch, tmp_path, lambda conv, prompt, n: {"status": "timeout"} if n == 1 else {"status": "done", "assistant_text": "PASS"})
    wid = save([node("s", "start.manual"), node("r", "flow.repeat", max=3, expression='text == "PASS"'),
                node("tester", "pipeline.agent", ducky="__new__", prompt="Test")],
               [go("s", "r"), go("r", "tester", "each")])
    out = runner.run_workflow(wid)
    assert out["ok"] and made == ["conv1", "conv2"] and [c for c, _p in sent] == ["conv1", "conv2"]
    assert out["node_outputs"]["r"] == {"attempt": 2, "passed": True}


# --------------------------------------------------------------------------- Message me


def _chat(title: str):
    from frontend.ui_web.project_chats import list_conversations, load_conversation

    rows = [c for c in list_conversations() if c.title == title]
    return [load_conversation(c.id) for c in rows]


def test_message_me_posts_to_the_reports_chat_or_the_chat_that_ran_it():
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import create_conversation

    wid = save([node("s", "start.manual"), node("m", "notify.message", inputs={"message": "Done: {{who}}"})], [go("s", "m")], name="Nightly")
    for _ in range(2):
        out = runner.run_workflow(wid, payload={"who": "duck"})
        assert out["ok"], out["error"]
    reports = _chat("Workflow reports")
    assert len(reports) == 1  # made once, found again by its name
    assert [m["text"] for m in reports[0].messages] == ["**Nightly**\n\nDone: duck"] * 2
    assert out["node_outputs"]["m"] == {"sent": True, "chat_id": reports[0].id}
    assert out["conv_id"] != reports[0].id  # the run's own chat id isn't overwritten

    caller = create_conversation(PanelSettings.load(), "", title="Me")
    runner.run_workflow(wid, payload={"who": "you"}, caller_conv_id=caller.id)
    assert _chat("Me")[0].messages[-1]["text"] == "**Nightly**\n\nDone: you"
    assert len(_chat("Workflow reports")[0].messages) == 2

    empty = save([node("s", "start.manual"), node("m", "notify.message")], [go("s", "m")])
    out = runner.run_workflow(empty)
    assert out["ok"] is False and "nothing to say" in out["error"]


def test_message_me_reports_a_run_that_breaks_before_it(monkeypatch):
    _tool_replies(monkeypatch, [{"ok": False, "error": "UEFN didn't open"}])
    graph = ([node("s", "start.manual"), node("t", "tool.call", "Open the island", name="x"),
              node("m", "notify.message", inputs={"message": "All good"}, on_fail=True, chat="Night shift")],
             [go("s", "t"), go("t", "m")])
    out = runner.run_workflow(save(*graph, name="Daily check"))
    assert out["ok"] is False
    assert _chat("Night shift")[0].messages[-1]["text"] == "**Daily check**\n\n❌ The run stopped at **Open the island**: UEFN didn't open"

    quiet = save(graph[0][:2] + [node("m", "notify.message", inputs={"message": "All good"}, chat="Quiet")], graph[1])
    assert runner.run_workflow(quiet)["ok"] is False and _chat("Quiet") == []  # only when switched on

    wid = save(*graph, name="Stopped")
    _tool_replies(monkeypatch, ["x"], on_call=lambda _n: runner.stop_workflow(wid))
    assert runner.run_workflow(wid)["error"] == runner.STOPPED
    assert len(_chat("Night shift")[0].messages) == 1  # Stop isn't a failure to report


# --------------------------------------------------------------------------- schedules


def test_schedules_run_in_the_background_and_never_twice_at_once(monkeypatch):
    release = threading.Event()
    started: list[str] = []

    def slow(wid: str, **_k: Any) -> dict[str, Any]:
        started.append(wid)
        release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(scheduler, "run_workflow", slow)
    cron = {"nodes": [node("c", "start.cron", interval_seconds=1)], "edges": []}
    first = store.save_workflow({"name": "Long daily test", "enabled": True, "graph": cron})["id"]
    second = store.save_workflow({"name": "Every minute", "enabled": True, "graph": cron})["id"]
    threads = scheduler._tick()
    try:
        assert len(threads) == 2  # the long one doesn't hold up the other
        assert scheduler._tick() == []  # still running: not started again
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert sorted(started) == sorted([first, second])


# --------------------------------------------------------------------------- the UEFN plugin's template


def _daily_template() -> dict[str, Any]:
    if not PLUGIN_JSON.is_file():
        pytest.skip("the uefn-plugins checkout isn't next to this repo")
    from backend.uefn_plugins.host import _automation_template_row

    doc = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    rows = (doc["contributes"].get("automations") or {}).get("templates") or []
    row = next(r for r in rows if r.get("id") == "daily-island-check")
    return _automation_template_row(row, str(doc["id"]))


def test_the_daily_check_template_is_wired_right():
    row = _daily_template()
    assert row["category"] == "Play tests" and row["plugin_id"] == "uefn"
    graph, specs = row["graph"], catalog.node_specs()
    assert all(n["type"] in specs for n in graph["nodes"])
    assert check_wires(graph, specs) == []
    cron = next(n for n in graph["nodes"] if n["type"] == "start.cron")
    assert cron["config"]["cron"] == "0 8 * * *"
    bundled = json.loads((Path(__file__).parents[2] / "frontend" / "bundled_agent_profiles.json").read_text(encoding="utf-8"))
    profiles = {p.get("id") for p in (bundled if isinstance(bundled, list) else bundled.get("profiles") or bundled.values()) if isinstance(p, dict)}
    assert {n["config"]["ducky"] for n in graph["nodes"] if n["type"] == "pipeline.agent"} <= profiles


def _run_daily(monkeypatch, *, servers_up: bool = True, passes_on: int = 2, uefn_running: bool = True) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    row = _daily_template()
    clock = Clock()
    page = status_page() if servers_up else status_page(Login="under_maintenance")
    monkeypatch.setitem(runner._DATA_HANDLERS, "fortnite.servers",
                        lambda cfg, _i, _p: servers.wait_for_servers(cfg, fetch=lambda: page, sleep=clock.sleep, clock=clock))
    uefn: list[str] = []
    monkeypatch.setattr(runner, "_uefn_node", lambda n, ntype, label, cfg: uefn.append(ntype) or {"ok": True, "id": n.get("id"), "type": ntype, "label": label, "result": {"ok": True}})

    def play(ntype: str, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        uefn.append(ntype)
        return {"ok": True, "result": {"running": uefn_running}} if ntype == "uefn.check" else {"ok": True, "result": {"playing": ntype == "uefn.game.start"}}

    monkeypatch.setattr(runner, "_play_node", play)
    agents: list[tuple[str, dict[str, Any]]] = []
    tries = {"n": 0}

    def agent(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        seat = cfg["_seat"]
        agents.append((seat, cfg))
        if seat == "test":
            tries["n"] += 1
            verdict = "PASS" if tries["n"] >= passes_on else "FAIL"
            text = f"Try {tries['n']}: the income tick {'works' if verdict == 'PASS' else 'never fires'}.\nRESULT: {verdict}"
        else:
            text = {"fix": "Fixed the income timer in tycoon.verse.", "private": "Island code: 1234-5678-9012",
                    "memory": "Memory: 41 MB of 100 MB"}[seat]
        return {"ok": True, "result": {"text": text, "files": []}}

    monkeypatch.setattr(runner, "_pipeline_agent", agent)
    wid = save(row["graph"]["nodes"], row["graph"]["edges"], name=row["name"])
    out = runner.run_workflow(wid, starter_id="start")
    out["uefn"] = uefn
    return out, agents


def test_the_daily_check_fixes_tests_again_then_reports(monkeypatch):
    out, agents = _run_daily(monkeypatch, passes_on=2)
    assert out["ok"], out["error"]
    assert [seat for seat, _cfg in agents] == ["test", "fix", "test", "private", "memory"]
    fix_cfg = agents[1][1]
    assert "RESULT: FAIL" in fix_cfg["context"] and fix_cfg["ducky"] == "verse-coder"  # the fixer reads the test report
    assert agents[0][1]["ducky"] == "tester"
    assert out["uefn"] == ["uefn.check", "uefn.wait_ready", "uefn.game.start", "uefn.game.stop", "uefn.game.start", "uefn.game.stop"]
    report = _chat("Workflow reports")[0].messages[-1]["text"]
    assert report.startswith("**Daily island check at 8 AM**\n\n✅ Today's island check passed (2 play test run(s)).")
    assert "Try 2: the income tick works." in report and "1234-5678-9012" in report and "41 MB of 100 MB" in report


def test_the_daily_check_gives_up_after_three_tries_and_says_what_still_fails(monkeypatch):
    out, agents = _run_daily(monkeypatch, passes_on=99, uefn_running=False)
    assert out["ok"], out["error"]
    assert [seat for seat, _cfg in agents] == ["test", "fix"] * 3  # no private version
    assert out["uefn"][:3] == ["uefn.check", "uefn.open_project", "uefn.game.start"]  # UEFN was closed: opened it
    report = _chat("Workflow reports")[0].messages[-1]["text"]
    assert "❌ Today's island check still fails after 3 tries" in report
    assert "Try 3: the income tick never fires." in report and "Fixed the income timer" in report


def test_the_daily_check_waits_for_the_servers_and_says_when_they_stay_down(monkeypatch):
    out, agents = _run_daily(monkeypatch, servers_up=False)
    assert out["ok"] and agents == [] and out["uefn"] == []
    report = _chat("Workflow reports")[0].messages[-1]["text"]
    assert "⏸ Fortnite's servers were still down" in report and "Login: under maintenance" in report
