"""Anonymous usage stats: the allowlist, the queue, the switch, the batch."""

from __future__ import annotations

from typing import Any

import pytest

from frontend import usage_events as ev
from frontend import usage_stats as us


@pytest.fixture(autouse=True)
def _fresh_module():
    us._enabled_cache = None
    us._run_meta.clear()
    us._conv_run.clear()
    us._mem_queue.clear()
    us.send_override = None
    us.registry_override = None
    us._last_uefn_mode = None
    us._install_fresh = False
    us._update_check_sent = False
    us._stop.set()
    yield
    us.send_override = None
    us.registry_override = None
    us._enabled_cache = None
    us._stop.set()


SAMPLES: dict[str, dict[str, Any]] = {
    "app.open": {"version": "1.2.401", "os": "win11", "install_age_s": 10},
    "app.heartbeat": {"uefn_up": True},
    "app.close": {"uptime_s": 5400},
    "update.installed": {"from": "1.2.400", "to": "1.2.401"},
    "update.check": {"status": "update_available", "channel": "installed", "pack": True},
    "setup.step": {"step": "connect_ai"},
    "setup.first_message": {"within": "later"},
    "setup.plugin": {"plugin_id": "anthropic", "ok": False},
    "setup.ai": {"provider": "gemini", "state": "not_logged_in"},
    "tour.end": {"tour": "app.shell", "how": "skipped", "at": "3"},
    "uefn.connect": {"ok": False, "reason": "port_busy"},
    "uefn.popup_watchdog": {"kind": "save"},
    "agent.login": {"provider": "codex", "ok": True, "reason": "other"},
    "chat.turn": {"provider": "claude", "model_family": "claude", "outcome": "ok", "duration_s": 70, "tool_calls_n": 3},
    "tool.call": {"tool": "spawn_actor", "ok": True, "duration_s": 3.4},
    "skill.used": {"skill": "verse"},
    "workflow.run": {"outcome": "ok", "nodes_n": 7},
    "plugin.install": {"plugin_id": "uefn-physics"},
    "plugin.remove": {"plugin_id": "uefn-physics"},
    "feature.open": {"tab": "workflows"},
    "error.code": {"code": "agent_timeout", "where": "agent"},
    "agent.stuck": {"provider": "codex", "after_s": 90},
    "ui.crash": {"code": "render"},
    "perf.sample": {"startup_s": 3.2, "idle_cpu_pct": 0.4, "memory_mb": 700},
}


def test_every_listed_event_validates_and_buckets() -> None:
    for name, props in SAMPLES.items():
        cleaned = ev.validate(name, props)
        assert cleaned is not None and cleaned, name
        for v in cleaned.values():
            assert isinstance(v, str) and v
    assert ev.validate("app.close", {"uptime_s": 5400}) == {"uptime": "2h"}
    assert ev.validate("chat.turn", SAMPLES["chat.turn"])["tool_calls"] == "1-5"
    assert ev.validate("chat.turn", SAMPLES["chat.turn"])["duration"] == "5m"
    assert ev.validate("tool.call", SAMPLES["tool.call"]) == {"tool": "spawn_actor", "ok": "yes", "duration": "10s"}
    assert ev.validate("perf.sample", SAMPLES["perf.sample"]) == {"startup": "5s", "idle_cpu": "1pct", "memory": "1gb"}


def test_free_text_and_unknown_names_never_pass() -> None:
    assert ev.validate("chat.text", {"text": "hello"}) is None
    cleaned = ev.validate("chat.turn", {"provider": "C:/Users/x", "outcome": "ok", "prompt": "secret"})
    assert cleaned == {"outcome": "ok"}
    assert ev.validate("error.code", {"code": "Traceback (most recent call last)", "where": "agent"}) == {"where": "agent"}
    assert ev.validate("tool.call", {"tool": "Has Space", "ok": "yes"}) == {"ok": "yes"}
    assert ev.validate("app.open", {"version": "1.2"}) == {}
    assert ev.validate("feature.open", {"tab": "my-secret-project"}) == {}


def test_error_source_becomes_a_code_never_a_message() -> None:
    assert ev.code_of("Agent stream") == "agent_stream"
    assert ev.where_of("uefn listener") == "bridge"
    assert ev.where_of("verse build") == "uefn"
    assert ev.where_of("plugin install") == "store"
    assert ev.where_of("") == "other"
    assert ev.provider_of("claude_code") == "claude"
    assert ev.provider_of("ducky", "anthropic") == "claude"
    assert ev.provider_of("ducky", "ollama") == "ollama"
    assert ev.model_family("gpt-6-luna", "openai") == "gpt"


def test_install_id_is_random_stable_and_resettable() -> None:
    first = us.install_id()
    assert len(first) == 32 and int(first, 16) >= 0
    assert us.install_id() == first
    us.reset_install_id()
    second = us.install_id()
    assert len(second) == 32 and second != first


def test_track_queues_and_flush_sends_one_batch_then_drops_rows() -> None:
    sent: list[dict[str, Any]] = []
    us.send_override = lambda body: sent.append(body) or True
    for _ in range(60):
        assert us.track("tool.call", tool="spawn_actor", ok=True, duration_s=0.005)
    assert us.track("chat.text", text="nope") is False
    assert us.queued() == 60
    assert us.flush() is True
    assert len(sent) == 1
    body = sent[0]
    assert set(body) == {"installId", "appVersion", "events"}
    assert body["installId"] == us.install_id()
    assert len(body["events"]) == 60
    assert body["events"][0]["name"] == "tool.call"
    assert body["events"][0]["props"] == {"tool": "spawn_actor", "ok": "yes", "duration": "1s"}
    assert us.queued() == 0


def test_failed_send_keeps_the_rows() -> None:
    us.send_override = lambda body: False
    us.track("feature.open", tab="chat")
    assert us.flush() is False
    assert us.queued() == 1
    us.send_override = lambda body: True
    assert us.flush() is True
    assert us.queued() == 0


def test_switch_off_means_no_queue_and_no_network() -> None:
    sent: list[dict[str, Any]] = []
    us.send_override = lambda body: sent.append(body) or True
    us.track("feature.open", tab="chat")
    assert us.queued() == 1
    us.set_enabled(False)
    assert us.queued() == 0, "turning it off empties the queue"
    for _ in range(10):
        assert us.track("feature.open", tab="chat") is False
    assert us.flush() is False
    assert sent == []
    assert us.queued() == 0
    us.set_enabled(True)
    assert us.track("feature.open", tab="chat")


def test_chat_turn_counts_its_tools_and_first_message_once() -> None:
    us.run_begin("run-1", "conv-1", coding_agent="codex", provider="openai", model="gpt-6-luna")
    us.tool_done("spawn_actor", ok=True, duration_ms=12, conv_id="conv-1", args={"label": "Cube"})
    us.tool_done("skill_read_subskill", ok=True, duration_ms=12, conv_id="conv-1", args={"pack_id": "verse", "subskill_id": "ui"})
    us.run_end("run-1", "done")
    us.run_begin("run-2", "conv-1", coding_agent="ducky", provider="anthropic", model="claude-opus-5-5")
    us.run_end("run-2", "error")
    rows, _ = us._take(100)
    names = [r["name"] for r in rows]
    assert names.count("setup.first_message") == 1
    assert names.count("tool.call") == 2
    assert names.count("skill.used") == 1
    turns = [r["props"] for r in rows if r["name"] == "chat.turn"]
    assert turns[0] == {"provider": "codex", "model_family": "gpt", "outcome": "ok", "duration": "10s", "tool_calls": "1-5"}
    assert turns[1]["provider"] == "claude" and turns[1]["outcome"] == "error" and turns[1]["tool_calls"] == "0"
    assert not any("Cube" in str(r) for r in rows)


def test_turn_outcome_tells_limits_logins_and_closed_apps_from_errors() -> None:
    cases = [("done", "", "ok"), ("error", "", "error"), ("error", "You've hit your session limit · resets 1pm", "limit"),
             ("needs_login", "", "login"), ("interrupted", "host exited", "interrupted"), ("weird", "", "other")]
    for i, (reason, detail, _) in enumerate(cases):
        us.run_begin(f"r{i}", f"c{i}", coding_agent="claude_code", provider="anthropic", model="claude-opus-5-5")
        us.run_end(f"r{i}", reason, detail)
    rows, _ = us._take(100)
    outcomes = [r["props"]["outcome"] for r in rows if r["name"] == "chat.turn"]
    assert outcomes == [want for _, _, want in cases]


def test_a_failed_turn_says_what_kind_of_failure_and_nothing_else() -> None:
    cases = [("error", "codex exited with code 1", "crash"), ("error", "Error: not logged in", "auth"),
             ("error", "spawn C:/Users/x/codex.exe: file not found", "not_found"), ("done", "", None)]
    for i, (reason, detail, _) in enumerate(cases):
        us.run_begin(f"k{i}", f"ck{i}", coding_agent="codex", provider="openai", model="gpt-6-luna")
        us.run_end(f"k{i}", reason, detail)
    rows, _ = us._take(100)
    turns = [r["props"] for r in rows if r["name"] == "chat.turn"]
    assert [t.get("kind") for t in turns] == [want for _, _, want in cases]
    assert not any("Users" in str(t) or "codex.exe" in str(t) for t in turns)


def test_overloaded_credit_and_rate_limit_stops_are_limits() -> None:
    assert us._turn_outcome("error", "Error code: 529 overloaded_error") == "limit"
    assert us._turn_outcome("error", "Your credit balance is too low") == "limit"
    assert us._turn_outcome("error", "rate_limit_error: slow down") == "limit"
    assert us._turn_outcome("error", "Error code: 400 invalid_request_error") == "error"


def test_update_check_is_sent_once_per_session() -> None:
    us.update_checked("paused", "installed", False)
    us.update_checked("up_to_date", "installed", False)
    rows, _ = us._take(100)
    checks = [r["props"] for r in rows if r["name"] == "update.check"]
    assert checks == [{"status": "paused", "channel": "installed", "pack": "no"}]


def test_perf_sample_counts_running_turns(monkeypatch) -> None:
    monkeypatch.setattr(us, "_meta_get", lambda key: "0")
    monkeypatch.setattr(us, "_meta_set", lambda key, value: None)
    us.run_begin("busy", "conv-busy", coding_agent="claude_code", provider="anthropic", model="claude-opus-5-5")
    us._maybe_perf_sample()
    rows, _ = us._take(100)
    sample = next(r["props"] for r in rows if r["name"] == "perf.sample")
    assert sample["agents"] == "1"
    assert ev.validate("perf.sample", {"agents_running_n": 0}) == {"agents": "0"}
    assert ev.validate("perf.sample", {"agents_running_n": 3}) == {"agents": "2-4"}


def test_ui_track_accepts_only_ui_events_and_uefn_transitions_count_once() -> None:
    assert us.ui_track("feature.open", {"tab": "settings"})
    assert us.ui_track("error.code", {"code": "x", "where": "ui"}) is False
    us.uefn_mode("offline")
    us.uefn_mode("online")
    us.uefn_mode("online")
    us.uefn_mode("wedged")
    rows, _ = us._take(100)
    connects = [r["props"] for r in rows if r["name"] == "uefn.connect"]
    assert connects == [{"ok": "yes"}, {"ok": "no", "reason": "timeout"}]


@pytest.mark.parametrize(("window", "reason"), [(False, "not_running"), (True, "lost"), (None, "lost")])
def test_a_dropped_uefn_link_says_whether_uefn_was_closed(monkeypatch, window, reason) -> None:
    monkeypatch.setattr(us, "_uefn_window_open", lambda: window)
    us.uefn_mode("online")
    us.uefn_mode("offline")
    rows, _ = us._take(100)
    connects = [r["props"] for r in rows if r["name"] == "uefn.connect"]
    assert connects[-1] == {"ok": "no", "reason": reason}


def test_debug_state_has_no_event_content() -> None:
    us.track("error.code", code="boom", where="ui")
    state = us.debug_state()
    assert set(state) == {"enabled", "queued", "thread_alive", "runs_open", "install_id_set"}
    assert state["queued"] == 1


def test_setup_steps_and_connect_ai_values() -> None:
    assert ev.validate("setup.step", {"step": "defaults"}) == {"step": "defaults"}
    assert ev.validate("setup.step", {"step": "closed"}) == {"step": "closed"}
    assert ev.validate("setup.ai", {"provider": "claude", "state": "plan"}) == {"provider": "claude", "state": "plan"}
    assert ev.validate("setup.ai", {"provider": "claude", "state": "my key is sk-123"}) == {"provider": "claude"}
    assert ev.validate("tour.end", {"tour": "App Shell!", "how": "done", "at": "2"}) == {"how": "done", "at": "2"}
    assert us.ui_track("setup.ai", {"provider": "codex", "state": "key"})
    assert us.ui_track("tour.end", {"tour": "chat.composer", "how": "done", "at": "4"})
    assert us.ui_track("setup.plugin", {"plugin_id": "anthropic", "ok": "yes"}) is False, "backend-only"


def test_first_start_takes_over_the_id_setup_made() -> None:
    us.reset_install_id()
    setup_id = "ab" * 16
    us.registry_override = {"InstallId": setup_id, "InstallSince": "1700000000"}
    assert us.install_id() == setup_id
    assert us._install_fresh
    assert us.install_age_s() > 0
    # The app's id stays the app's from then on, whatever the key says later.
    us.registry_override["InstallId"] = "cd" * 16
    assert us.install_id() == setup_id


def test_a_bad_setup_id_is_ignored() -> None:
    us.reset_install_id()
    us.registry_override = {"InstallId": r"C:\Users\someone"}
    fresh = us.install_id()
    assert len(fresh) == 32 and fresh != us.registry_override["InstallId"]


def test_the_key_mirrors_the_id_and_the_switch() -> None:
    us.registry_override = {}
    us._mirror(True)
    assert us.registry_override["UsageStats"] == 1
    assert us.registry_override["InstallId"] == us.install_id()
    assert int(us.registry_override["InstallSince"]) > 0
    us.set_enabled(False)
    assert us.registry_override["UsageStats"] == 0
    us.set_enabled(True)
    assert us.registry_override["UsageStats"] == 1


def test_no_registry_outside_the_installed_app() -> None:
    # Source runs and tests (not frozen) never read or write HKCU.
    assert us._registry_on() is False
    assert us._reg_read() == {}


def test_a_coding_agent_turn_counts_the_tools_its_cli_ran() -> None:
    # Oct 22 2026: Claude Code / Codex calls are counted by the shared tool server, a process
    # with no turns, so 86% of turns (20-minute ones included) said "0 tool calls".
    us.run_begin("run-cli", "conv-cli", coding_agent="claude_code", model="claude-sonnet-5-5")
    for _ in range(7):
        us.turn_tool("run-cli")
    us.turn_tool("some-other-run")
    us.run_end("run-cli", "done")
    rows, _ = us._take(100)
    turns = [r["props"] for r in rows if r["name"] == "chat.turn"]
    assert turns == [{"provider": "claude", "model_family": "claude", "outcome": "ok", "duration": "10s", "tool_calls": "6-20"}]
    assert not [r for r in rows if r["name"] == "tool.call"]  # the tool server counts those


def test_a_failed_tool_call_is_not_counted_again_as_an_error(monkeypatch, tmp_path) -> None:
    # Ducky's agent logged each failed call to Settings → Errors as "agent": the top error code
    # (511 in a week) was the same failures tool.call already counted.
    from frontend import error_log

    monkeypatch.setattr(error_log, "_use_db", lambda: False)
    monkeypatch.setattr(error_log, "_path", lambda name: tmp_path / name)
    monkeypatch.setattr(error_log, "_trim_file", lambda name: None)
    error_log.record_error("agent", "spawn_actor: no such class", count=False)
    error_log.record_error("agent", "turn crashed after 3.0s")
    rows, _ = us._take(100)
    from frontend import __version__

    assert [r["props"] for r in rows if r["name"] == "error.code"] == [
        {"code": "agent", "where": "agent", "kind": "crash", "version": __version__}]
    assert ev.where_of("chat_slow") == ev.where_of("chat_timeout") == "ui"


def test_error_kind_comes_from_the_message_but_the_message_never_leaves(monkeypatch, tmp_path) -> None:
    from frontend import error_log

    assert ev.kind_of("Error code: 400 invalid_request_error") == "bad_request"
    assert ev.kind_of("session unavailable") == "session_dead"
    assert ev.kind_of("timed out waiting for listener idle") == "busy"
    assert ev.kind_of("something odd") == "other"
    monkeypatch.setattr(error_log, "_use_db", lambda: False)
    monkeypatch.setattr(error_log, "_path", lambda name: tmp_path / name)
    monkeypatch.setattr(error_log, "_trim_file", lambda name: None)
    error_log.record_error("agent stream", "Error code: 400 invalid_request_error in my_secret_project")
    us.agent_crash("codex")
    rows, _ = us._take(100)
    errors = [r["props"] for r in rows if r["name"] == "error.code"]
    assert errors[0]["kind"] == "bad_request" and errors[0]["code"] == "agent_stream"
    assert errors[1] == {"code": "agent_crash_codex", "where": "agent", "kind": "crash", "version": errors[0]["version"]}
    assert "secret" not in str(rows) and "400" not in str(rows)


def test_an_uncaught_thread_exception_is_one_error_code(monkeypatch, tmp_path) -> None:
    import sys
    import threading

    from frontend import error_log

    monkeypatch.setattr(error_log, "_use_db", lambda: False)
    monkeypatch.setattr(error_log, "_path", lambda name: tmp_path / name)
    monkeypatch.setattr(error_log, "_trim_file", lambda name: None)
    monkeypatch.setattr(error_log, "_crash_hooks_on", False)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the "previous" hook: stay quiet
    error_log.install_crash_hooks()
    ducky_mod: dict[str, Any] = {"__name__": "frontend.fake_panel"}  # a frame Ducky owns
    exec("def open_tab():\n    raise ValueError('C:/Users/someone/secret.txt is bad')", ducky_mod)
    t = threading.Thread(target=ducky_mod["open_tab"])
    t.start()
    t.join()
    rows, _ = us._take(100)
    errors = [r["props"] for r in rows if r["name"] == "error.code"]
    assert len(errors) == 1
    assert errors[0]["code"] == "py.valueerror" and errors[0]["kind"] == "crash"
    assert errors[0]["at"] == "frontend.fake_panel.open_tab"
    assert "secret" not in str(rows)


def test_a_start_after_a_run_that_never_closed_is_one_unclean_exit(monkeypatch) -> None:
    from frontend import __version__, error_log

    meta: dict[str, str] = {us.META_LAST_VERSION: "1.2.400"}
    monkeypatch.setattr(us, "_meta_get", meta.get)
    monkeypatch.setattr(us, "_meta_set", meta.__setitem__)
    monkeypatch.setattr(us, "start", lambda: None)
    monkeypatch.setattr(error_log, "install_crash_hooks", lambda: None)
    us.send_override = lambda body: True

    def unclean() -> list[dict[str, Any]]:
        rows, _ = us._take(100)
        us._mem_queue.clear()
        return [r["props"] for r in rows if r["name"] == "error.code" and r["props"]["code"] == "app_unclean_exit"]

    us.app_opened()
    us.app_closing()
    us.app_opened()
    assert unclean() == []
    us.app_opened()
    assert unclean() == [{"code": "app_unclean_exit", "where": "ui", "kind": "crash", "version": __version__}]


def test_outside_agents_refusals_fail_and_ducky_call_tool_counts_as_its_tool(monkeypatch) -> None:
    from backend.bridge import shared_mcp

    def result(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "isError": False}

    assert shared_mcp._refused(result('{"ok":false,"error":"cannot complete a Verify leaf"}'))
    assert shared_mcp._refused(result('ERROR: pack_id is required'))
    assert not shared_mcp._refused(result('{"ok":true,"plan":{}}'))
    assert not shared_mcp._refused(result('{"found": false, "known": []}'))
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(us, "tool_done", lambda name, *, ok, **_: seen.append((name, ok)))
    shared_mcp._usage_tool_done("ducky_call_tool", False, 0.0, None, {"name": "set_actor_label", "arguments": {}})
    assert seen == [("set_actor_label", False)]
