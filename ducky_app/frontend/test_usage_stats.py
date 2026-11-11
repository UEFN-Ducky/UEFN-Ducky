"""Anonymous usage stats: the allowlist, the queue, the switch, the batch."""

from __future__ import annotations

import time
from typing import Any

import pytest

from frontend import usage_events as ev
from frontend import usage_stats as us


@pytest.fixture(autouse=True)
def _fresh_module():
    us._enabled_cache = None
    us._run_meta.clear()
    us._conv_run.clear()
    us._last_failed.clear()
    us._mem_queue.clear()
    us._refused_in_a_row = 0
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
    "setup.source": {"source": "youtube"},
    "setup.rating": {"stars": "4"},
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
    "revision.op": {"op": "pull", "outcome": "blocked", "reason": "both_changed", "took_s": 4},
    "remote.connect": {"outcome": "timeout"},
    "remote.link": {"link": "live", "candidate": "relay", "connect_ms": 812},
    "remote.quality": {"fps_n": 24, "rtt_ms": 120, "loss_pct": 0.5, "client": "android_app", "net": "cell"},
    "remote.input": {"lag_ms": 90, "dropped_n": 0},
    "remote.load": {"first_frame_ms": 2500, "page_ms": 600},
    "remote.end": {"why": "network", "length_s": 400, "fail_stage": "none", "viewer_version": "1.4.2"},
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
    assert ev.validate("remote.quality", SAMPLES["remote.quality"]) == {
        "fps": "15-30", "rtt": "150ms", "loss": "1pct", "client": "android_app", "net": "cell"}
    assert ev.validate("remote.quality", {"fps_n": 14.5, "height_px": 1080, "freezes_n": 2, "jitter_ms": 5,
                                          "bitrate_kbps": 1500}) == {
        "fps": "under15", "res": "1080p", "freezes": "1-3", "jitter": "10ms", "bitrate": "2mbps"}
    assert ev.validate("remote.quality", {"fps": "30+", "res": "720p"}) == {"fps": "30+", "res": "720p"}
    assert ev.validate("remote.input", {"lag_ms": 500, "dropped_n": 3}) == {"lag": "more", "dropped": "1-5"}
    assert ev.validate("remote.load", SAMPLES["remote.load"]) == {"first_frame": "3s", "page": "1s"}
    assert ev.validate("remote.end", SAMPLES["remote.end"]) == {
        "why": "network", "length": "20m", "fail_stage": "none", "viewer_version": "1.4.2"}


def test_remote_events_drop_anything_identifying() -> None:
    leaky = {"ip": "10.0.0.4", "device_id": "a1b2c3", "reason": "No path from 10.0.0.4", "client": "Pixel 9 / Chrome",
             "net": "MyHomeWifi", "viewer_version": "1.4.2-dev+me", "why": "user closed it at 10.0.0.4",
             "fail_stage": "C:/Users/x", "res": "1920x1080", "fps": "lots"}
    for name in ("remote.connect", "remote.link", "remote.quality", "remote.input", "remote.load", "remote.end"):
        assert ev.validate(name, leaky) == {}, name


def test_free_text_and_unknown_names_never_pass() -> None:
    assert ev.validate("chat.text", {"text": "hello"}) is None
    cleaned = ev.validate("chat.turn", {"provider": "C:/Users/x", "outcome": "ok", "prompt": "secret"})
    assert cleaned == {"outcome": "ok"}
    assert ev.validate("error.code", {"code": "Traceback (most recent call last)", "where": "agent"}) == {"where": "agent"}
    assert ev.validate("tool.call", {"tool": "Has Space", "ok": "yes"}) == {"ok": "yes"}
    assert ev.validate("app.open", {"version": "1.2"}) == {}
    assert ev.validate("feature.open", {"tab": "my-secret-project"}) == {}


@pytest.fixture
def meta(monkeypatch) -> dict[str, str]:
    """ducky.db meta as a dict: shared by every 'process' in a test, never the real DB."""
    store: dict[str, str] = {}
    monkeypatch.setattr(us, "_meta_get", store.get)
    monkeypatch.setattr(us, "_meta_set", store.__setitem__)
    return store


def test_net_fail_is_sent_once_a_day_across_processes(meta) -> None:
    """One 1.2.483 PC sent 7 update_check timeouts in a day, one per process."""
    for _ in range(2):  # two processes: each starts with an empty in-memory set
        us._net_fail_sent.clear()
        us.net_fail("update_check", "timeout", "none")
    rows, _ = us._take(100)
    assert len([r for r in rows if r["name"] == "error.code"]) == 1
    meta["usage.net_fail.update_check.timeout"] = "2000-01-01"  # sent on an earlier day
    us._net_fail_sent.clear()
    us.net_fail("update_check", "timeout", "none")
    rows, _ = us._take(100)
    assert len([r for r in rows if r["name"] == "error.code"]) == 2


def test_net_fail_says_where_the_route_died_once_per_session(meta) -> None:
    us._net_fail_sent.clear()
    us.net_fail("update_check", "timeout", "pac")
    us.net_fail("update_check", "timeout", "pac")  # the same failure again: not counted twice
    us.net_fail("update_check", "dns", "none")
    rows, _ = us._take(100)
    errors = [r["props"] for r in rows if r["name"] == "error.code"]
    assert [(e["code"], e["kind"], e["net_stage"], e["proxy"], e["where"]) for e in errors] == [
        ("update_check", "timeout", "timeout", "pac", "store"),
        ("update_check", "offline", "dns", "none", "store"),
    ]
    assert ev.validate("error.code", {"net_stage": "carrier_pigeon", "proxy": "10.0.0.1:8080"}) == {}


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
    # 60 identical calls go as one counted row (hourly batches, Oct 30 2026).
    assert len(body["events"]) == 1
    row = body["events"][0]
    assert row["name"] == "tool.call" and row["n"] == 60 and row["t0"] <= row["ts"]
    assert row["props"] == {"tool": "spawn_actor", "ok": "yes", "duration": "1s"}
    assert us.queued() == 0


def test_a_batch_holds_at_most_batch_max_distinct_rows_and_the_rest_waits(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []
    us.send_override = lambda body: sent.append(body) or True
    monkeypatch.setattr(us, "BATCH_MAX", 3)
    for tab in ("chat", "editor", "terminal", "plan", "chat"):
        us.track("feature.open", tab=tab)
    assert us.flush() is False, "one distinct row left for the next flush"
    assert [e["props"]["tab"] for e in sent[0]["events"]] == ["chat", "editor", "terminal"]
    assert sent[0]["events"][0]["n"] == 2
    assert us.queued() == 1
    assert us.flush() is True and sent[1]["events"][0]["props"]["tab"] == "plan"
    assert us.queued() == 0


def test_failed_send_keeps_the_rows() -> None:
    us.send_override = lambda body: False
    us.track("feature.open", tab="chat")
    assert us.flush() is False
    assert us.queued() == 1
    us.send_override = lambda body: True
    assert us.flush() is True
    assert us.queued() == 0


def test_a_batch_the_store_keeps_refusing_is_dropped_not_resent_forever() -> None:
    # A row the Store can never take once held a PC's whole queue (and its signed-in status) for a day.
    answers = iter(["refused", "failed", "refused", "refused", "ok"])
    sent: list[dict[str, Any]] = []
    us.send_override = lambda body: sent.append(body) or next(answers)
    us.track("feature.open", tab="chat")
    assert us.flush() is False and us.queued() == 1   # refused once: kept
    assert us.flush() is False and us.queued() == 1   # offline: not a refusal
    assert us.flush() is False and us.queued() == 1   # refused twice
    assert us.flush() is False                         # third refusal: dropped, reported
    rows, _ = us._take(10)
    assert [r["name"] for r in rows] == ["error.code"] and rows[0]["props"]["code"] == "usage_batch_refused"
    assert us.flush() is True and us.queued() == 0


def test_a_store_that_is_busy_or_rate_limited_never_drops_the_batch(monkeypatch) -> None:
    # The Store answers 200 {"error": ...}; a failed store write says "try again:".
    from frontend import duckyos_account

    errors = iter(["try again: store busy (db timeout)"] * 5 + ["too soon: one batch per install every 30 s", ""])
    monkeypatch.setattr(duckyos_account, "api_request", lambda *a, **k: (200, {"payload": {"error": next(errors)}}, ""))
    us.track("feature.open", tab="chat")
    for _ in range(6):
        assert us.flush() is False and us.queued() == 1   # kept every time, never dropped
    assert us.flush() is True and us.queued() == 0


def test_a_store_that_answers_404_429_or_5xx_never_drops_the_batch(monkeypatch) -> None:
    from frontend import duckyos_account

    answers = iter([(404, None, ""), (429, {"error": "slow down"}, ""), (503, None, ""), (200, {"payload": {}}, "")])
    monkeypatch.setattr(duckyos_account, "api_request", lambda *a, **k: next(answers))
    us.track("feature.open", tab="chat")
    for _ in range(3):
        assert us.flush() is False and us.queued() == 1
    assert us.flush() is True and us.queued() == 0


def _refusal_reports() -> list[dict[str, Any]]:
    rows, _ = us._take(100)
    return [r["props"] for r in rows if r["name"] == "error.code" and r["props"].get("code") == "usage_batch_refused"]


def test_a_refused_batch_reports_the_stores_one_word_reason(monkeypatch) -> None:
    from frontend import duckyos_account

    monkeypatch.setattr(duckyos_account, "api_request", lambda *a, **k: (400, {"error": "bad_event"}, ""))
    us.track("feature.open", tab="chat")
    for _ in range(us.REFUSED_DROP_AFTER):
        assert us.flush() is False
    reports = _refusal_reports()
    assert len(reports) == 1 and reports[0]["reason"] == "bad_event" and reports[0]["kind"] == "bad_request"


def test_reason_token_is_one_short_word_never_the_detail() -> None:
    assert us.reason_token("bad_event") == "bad_event"
    assert us.reason_token("Unknown event name: chat.text from C:/Users/x") == "unknown_event_name"
    assert us.reason_token("schema mismatch in a very very long message", 400) == "schema_mismatch_in_a_ver"
    assert us.reason_token("", 418) == "http_418"
    assert us.reason_token("Payload Too Large", 413) == "too_big"
    # anr9c4x6p1h8j: the usual refusals as fixed words.
    assert us.reason_token("", 413) == "too_big"
    assert us.reason_token("bad install id: abc") == "install_id"
    assert us.reason_token("origin not allowed", 403) == "origin"
    assert us.reason_token("rate limited", 400) == "rate"
    assert us.reason_token("", 502) == "server_5xx"
    assert us.reason_token("") == "unknown"
    assert ev.SLUG_RE.match(us.reason_token("schema mismatch in a very very long message"))


def test_a_batch_refused_as_too_large_is_split_and_both_halves_sent(monkeypatch) -> None:
    from frontend import duckyos_account

    sent: list[int] = []

    def store(method: str, path: str, body: dict[str, Any], **_: Any) -> tuple[int, Any, str]:
        sent.append(len(body["events"]))
        return (413, None, "") if len(sent) == 1 else (200, {"payload": {}}, "")

    monkeypatch.setattr(duckyos_account, "api_request", store)
    for tab in ("chat", "editor", "plan", "usage"):
        us.track("feature.open", tab=tab)
    assert us.flush() is False
    assert sent == [4, 2, 2], "the whole batch, then each half once"
    assert us.queued() == 0 and _refusal_reports() == []


def test_a_too_large_half_that_is_refused_again_is_dropped_and_reported() -> None:
    answers = iter(["refused:too_large", "ok", "refused:too_large"])
    sent: list[int] = []
    us.send_override = lambda body: sent.append(len(body["events"])) or next(answers)
    for tab in ("chat", "editor", "plan"):
        us.track("feature.open", tab=tab)
    assert us.flush() is False
    assert sent == [3, 1, 2]
    reports = _refusal_reports()
    assert [r["reason"] for r in reports] == ["too_large"]


def test_a_refused_413_reports_too_big(monkeypatch) -> None:
    from frontend import duckyos_account

    monkeypatch.setattr(duckyos_account, "api_request", lambda *a, **k: (413, None, ""))
    us.track("feature.open", tab="chat")  # one row: nothing to split, so it counts toward dropping
    for _ in range(us.REFUSED_DROP_AFTER):
        assert us.flush() is False
    assert [r["reason"] for r in _refusal_reports()] == ["too_big"]


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
    v = {"v": us._app_version()} if ev.VERSION_RE.match(us._app_version()) else {}
    assert turns[0] == {"provider": "codex", "model_family": "gpt", "outcome": "ok", "duration": "10s", "tool_calls": "1-5",
                        "tool_fails": "0", **v}
    assert turns[1]["provider"] == "claude" and turns[1]["outcome"] == "error" and turns[1]["tool_calls"] == "0"
    assert not any("Cube" in str(r) for r in rows)


def test_source_control_and_remote_view_report_words_only() -> None:
    us.revision("push", {"ok": True, "checked_in": 3}, 42)
    us.revision("pull", {"ok": True, "up_to_date": True}, 0.5)
    us.revision("pull", {"ok": False, "both_changed": [{"rel": "Content/Secret.verse"}], "error": "C:/Users/x"}, 2)
    us.revision("push", {"ok": False, "error": "the remote has changes this island doesn't have: pull them first"}, 1)
    us.revision("push", {"ok": False, "error": "request timed out after 180 s"}, 200)
    us.remote_connect({"error": "desktop did not answer in time"})
    us.remote_connect({"answer": {"type": "answer", "sdp": "v=0"}})
    us.remote_link({"state": "failed", "reason": "No peer-to-peer path from 10.0.0.4", "at": 1.0})
    us.remote_link({"candidate": "relay", "connect_ms": 2400})
    rows, _ = us._take(100)
    got = [(r["name"], r["props"]) for r in rows]
    assert got == [
        ("revision.op", {"op": "push", "outcome": "ok", "took": "1m"}),
        ("revision.op", {"op": "pull", "outcome": "up_to_date", "took": "10s"}),
        ("revision.op", {"op": "pull", "outcome": "blocked", "reason": "both_changed", "took": "10s"}),
        ("revision.op", {"op": "push", "outcome": "blocked", "reason": "behind", "took": "10s"}),
        ("revision.op", {"op": "push", "outcome": "fail", "reason": "timeout", "took": "more"}),
        ("remote.connect", {"outcome": "timeout"}),
        ("remote.connect", {"outcome": "ok"}),
        ("remote.link", {"link": "failed"}),
        ("remote.link", {"candidate": "relay", "connect": "3s"}),
    ]
    assert "Secret" not in str(rows) and "10.0.0.4" not in str(rows)


def test_remote_link_forwards_quality_input_load_and_end() -> None:
    tag = {"client": "android_app", "net": "cell", "viewer_version": "1.4.2"}
    us.remote_link({**tag, "quality": {"fps_n": 24, "rtt_ms": 120, "loss_pct": 0.5, "height_px": 720, "ip": "10.0.0.4"}})
    us.remote_link({**tag, "input": {"lag_ms": 90, "dropped_n": 7, "name": "x"}})
    us.remote_link({"client": "phone_web", "net": "wifi", "load": {"first_frame_ms": 2500, "page_ms": 600}})
    us.remote_link({**tag, "state": "failed", "reason": "No path from 10.0.0.4",
                    "end": {"why": "network", "length_s": 400, "fail_stage": "ice_check", "reason": "Pixel 9 at 10.0.0.4"}})
    us.remote_link({"client": "Pixel 9", "net": "MyHomeWifi", "end": {"why": "free text"}})
    rows, _ = us._take(100)
    got = [(r["name"], r["props"]) for r in rows]
    assert got == [
        ("remote.quality", {"fps": "15-30", "rtt": "150ms", "loss": "1pct", "res": "720p", **tag}),
        ("remote.input", {"lag": "150ms", "dropped": "more", **tag}),
        ("remote.load", {"first_frame": "3s", "page": "1s", "client": "phone_web", "net": "wifi"}),
        ("remote.end", {"why": "network", "length": "20m", "fail_stage": "ice_check", **tag}),
        ("remote.link", {"link": "failed", **tag}),
        ("remote.end", {}),
    ]
    assert "10.0.0.4" not in str(rows) and "Pixel" not in str(rows)


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


def test_a_failed_turn_carries_the_app_version_and_a_fixed_list_reason(monkeypatch) -> None:
    """ank4r8t2b6x9p: 198 Claude turns a day died as kind:other with no build to tell them apart."""
    monkeypatch.setattr(us, "_app_version", lambda: "1.2.488")
    cases = [("MCP server uefn failed to connect", "mcp_connect"),
             ("Claude Code exited unsuccessfully (code 1)", "cli_exit_1"),
             ("codex process exited with code 3221225477", "cli_exit_3221225477"),
             ("No conversation found with session ID: 1234-abcd", "session_resume"),
             ("prompt is too long: 210000 tokens", "context_full"),
             ("connection reset by peer", "network"),
             ("Error: not logged in", "auth"),
             ("claude_code finished with no captured reply.", "empty"),
             ("something odd at C:/Users/x", "other")]
    for i, (detail, _) in enumerate(cases):
        us.run_begin(f"t{i}", f"ct{i}", coding_agent="claude_code", provider="anthropic", model="claude-opus-5-5")
        us.run_end(f"t{i}", "error", detail)
    us.run_begin("ok", "c-ok", coding_agent="claude_code")
    us.run_end("ok", "done")
    rows, _ = us._take(100)
    turns = [r["props"] for r in rows if r["name"] == "chat.turn"]
    assert [t.get("reason") for t in turns] == [want for _, want in cases] + [None]
    assert all(t["v"] == "1.2.488" for t in turns)
    assert turns[0]["kind"] == "offline" and turns[0]["outcome"] == "error"
    assert not any("Users" in str(t) or "1234" in str(t) for t in turns)


def test_a_stuck_report_carries_the_app_version(monkeypatch) -> None:
    monkeypatch.setattr(us, "_app_version", lambda: "1.2.488")
    us.stuck("claude_code", 300, tool="unreal__call_tool")
    rows, _ = us._take(100)
    assert [r["props"] for r in rows if r["name"] == "agent.stuck"] == [
        {"provider": "claude", "after": "5m", "tool": "unreal__call_tool", "v": "1.2.488"}]


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


def test_perf_sample_waits_for_steady_state(monkeypatch) -> None:
    """Nov 2 2026: the day's sample ran beside start-up threads and read as idle CPU."""
    stamped: list[str] = []
    monkeypatch.setattr(us, "_meta_get", lambda key: "0")
    monkeypatch.setattr(us, "_meta_set", lambda key, value: stamped.append(key))
    monkeypatch.setattr(us, "_opened_at", time.monotonic() - 60)
    us._maybe_perf_sample()
    assert stamped == [], "a minute after open: no sample, and the day's slot stays free"
    monkeypatch.setattr(us, "_opened_at", time.monotonic() - us.PERF_SETTLE_S - 1)
    us._maybe_perf_sample()
    rows, _ = us._take(100)
    assert stamped and any(r["name"] == "perf.sample" for r in rows)


def test_the_idle_loop_never_polls_the_queue_and_samples_cpu_once_a_day(monkeypatch, meta) -> None:
    """anf7q2w9e4r1t: usage-stats was the hot idle thread; it woke every 5 s to COUNT the queue."""
    import threading

    import psutil

    counts = {"queued": 0, "cpu": 0, "waits": []}
    monkeypatch.setattr(us, "queued", lambda: counts.__setitem__("queued", counts["queued"] + 1) or 0)
    monkeypatch.setattr(us, "_opened_at", time.monotonic() - us.PERF_SETTLE_S - 1)
    monkeypatch.setattr(us, "HEARTBEAT_S", 0.05)

    class Proc:
        def memory_info(self):
            return type("M", (), {"rss": 1 << 20})()

        def cpu_percent(self, interval=None):
            counts["cpu"] += 1
            return 0.1

        def threads(self):
            return []

        def children(self, recursive=False):
            return []

    monkeypatch.setattr(psutil, "Process", Proc)
    real_wait = us._wake.wait
    monkeypatch.setattr(us._wake, "wait", lambda t: counts["waits"].append(t) or real_wait(min(t, 0.05)))
    us._stop.clear()
    t = threading.Thread(target=us._loop, daemon=True)
    t.start()
    # Wait for a few heartbeat wakes, not a fixed 0.6 s: a loaded full-suite run starved the thread.
    deadline = time.monotonic() + 10
    while len(counts["waits"]) < 5 and time.monotonic() < deadline:
        time.sleep(0.05)
    us._stop.set()
    us._wake.set()
    t.join(2)
    rows, _ = us._take(1000)
    assert sum(r["name"] == "app.heartbeat" for r in rows) >= 3
    assert counts["queued"] == 0, "an idle wake never counts the queue"
    assert counts["cpu"] == 1, "one cpu_percent sample per PERF_SAMPLE_EVERY_S"


def test_the_idle_loop_sleeps_until_the_first_flush_not_a_5s_poll(monkeypatch) -> None:
    waits: list[float] = []

    def wait(t: float) -> bool:
        waits.append(t)
        us._stop.set()
        return False

    monkeypatch.setattr(us._wake, "wait", wait)
    us._stop.clear()
    us._loop()
    assert waits and abs(waits[0] - us.FIRST_FLUSH_S) < 1.0, waits


def test_perf_sample_names_the_busiest_thread_without_leaking_tool_names() -> None:
    import threading

    assert us.thread_slug("search-indexer-2") == "search_indexer"
    assert us.thread_slug("shared-mcp-call:roblox_call") == "shared_mcp_call"
    assert us.thread_slug("12345") == "native"
    me = threading.current_thread().native_id
    other = me + 1 if me + 1 != me else me + 2
    # This thread used 0.4 s of the window, another (no Python thread) 0.1 s.
    assert us._hot_thread({me: 1.0, other: 2.0}, {me: 1.4, other: 2.1}) == us.thread_slug(threading.current_thread().name)
    assert us._hot_thread({me: 1.0, other: 2.0}, {me: 1.0, other: 2.5}) == "native"
    assert us._hot_thread({me: 1.0}, {me: 1.0}) == ""
    assert us._hot_thread({}, {}) == ""
    assert ev.validate("perf.sample", {"idle_cpu_pct": 9.0, "hot": "search_indexer"})["hot"] == "search_indexer"


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


def test_setup_source_and_rating_come_from_the_ui() -> None:
    assert ev.validate("setup.source", {"source": "youtube"}) == {"source": "youtube"}
    assert ev.validate("setup.source", {"source": "my cousin Joe"}) == {}
    assert ev.validate("setup.rating", {"stars": "0"}) == {"stars": "0"}
    assert ev.validate("setup.rating", {"stars": "5"}) == {"stars": "5"}
    assert ev.validate("setup.rating", {"stars": "6"}) == {}
    assert us.ui_track("setup.source", {"source": "discord"})
    assert us.ui_track("setup.rating", {"stars": "4"})


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
    turns[0].pop("v", None)
    assert turns == [{"provider": "claude", "model_family": "claude", "outcome": "ok", "duration": "10s", "tool_calls": "6-20", "tool_fails": "0"}]
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
    assert ev.kind_of("Error code: 400 prompt is too long: 210000 tokens > 200000 maximum") == "too_long"
    assert ev.kind_of("Your input exceeds the context window of this model.") == "too_long"
    assert ev.kind_of("messages.3.content.1.image.source.base64: image exceeds 5 MB maximum") == "bad_picture"
    assert ev.kind_of("Invalid image format: application/octet-stream") == "bad_picture"
    assert set(ev.ERROR_KINDS) >= {"too_long", "bad_picture"}
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
    # What is sent (names and props), not the timestamps: a ts like 1794003196.6567516 holds "400".
    sent = str([(r["name"], r["props"]) for r in rows])
    assert "secret" not in sent and "400" not in sent


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
    import time

    from frontend import __version__, boot_report, error_log

    monkeypatch.setattr(boot_report, "_state", {"started": time.time() - 20, "stages": [["launch", 0.0], ["window_shown", 1.2]]})

    meta: dict[str, str] = {us.META_LAST_VERSION: "1.2.400"}
    monkeypatch.setattr(us, "_meta_get", meta.get)
    monkeypatch.setattr(us, "_meta_set", meta.__setitem__)
    monkeypatch.setattr(us, "start", lambda: None)
    monkeypatch.setattr(error_log, "install_crash_hooks", lambda: None)
    monkeypatch.setattr(us, "_memory_now", lambda: {"memory_mb": 700, "webview_mb": 2600})
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
    assert unclean() == [{"code": "app_unclean_exit", "where": "ui", "kind": "crash", "version": __version__,
                          "stage": "window_shown", "uptime": "1m", "memory": "1gb", "webview": "more"}]
    # The dead run's heartbeat said where it got and how long it ran: a long run, not a start-up crash.
    meta[us.META_LAST_SEEN] = '{"stage": "page_started", "uptime_s": 5000}'
    us.app_opened()
    row = unclean()[-1]
    assert row["stage"] == "page_started" and row["uptime"] == "2h"
    # A stage this list doesn't know is "other", never its name.
    meta[us.META_LAST_SEEN] = '{"stage": "C:/Users/x", "uptime_s": 40000}'
    us.app_opened()
    row = unclean()[-1]
    assert row["stage"] == "other" and row["uptime"] == "more"


def test_an_unclean_exit_carries_the_agents_memory_bucket_when_the_heartbeat_had_it(monkeypatch) -> None:
    from frontend import boot_report, error_log

    meta: dict[str, str] = {us.META_LAST_VERSION: "1.2.400"}
    monkeypatch.setattr(us, "_meta_get", meta.get)
    monkeypatch.setattr(us, "_meta_set", meta.__setitem__)
    monkeypatch.setattr(us, "start", lambda: None)
    monkeypatch.setattr(error_log, "install_crash_hooks", lambda: None)
    monkeypatch.setattr(us, "_memory_now", lambda: {"memory_mb": 700})
    us.send_override = lambda body: True

    def unclean() -> dict[str, Any]:
        rows, _ = us._take(100)
        us._mem_queue.clear()
        return [r["props"] for r in rows if r["name"] == "error.code" and r["props"]["code"] == "app_unclean_exit"][-1]

    state = {"started": time.time() - 20, "stages": [["launch", 0.0]],
             "heartbeat": {"agents_n": 6, "agents_mb": 1050, "top_other": [["chrome", 4500]]}}
    monkeypatch.setattr(boot_report, "_state", state)
    us.app_opened()
    assert json_loads(meta[us.META_LAST_SEEN])["agents_mb"] == 1050
    us.app_opened()   # the run above never closed
    row = unclean()
    assert row["agents"] == "2gb" and "agents_mb" not in row and "chrome" not in str(row)
    # An older boot.json heartbeat without agents_mb: no agents prop.
    monkeypatch.setattr(boot_report, "_state", {"started": time.time() - 20, "stages": [["launch", 0.0]],
                                                "heartbeat": {"free_gb": 2.0}})
    us._note_alive()
    us.app_opened()
    assert "agents" not in unclean()


def json_loads(text: str) -> Any:
    import json

    return json.loads(text)


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


def test_cmd_kind_names_only_the_sort_of_command() -> None:
    assert ev.cmd_kind("py -3 -m pytest ducky_app/frontend -q") == "test"
    assert ev.cmd_kind("cargo test --test usage") == "test"
    assert ev.cmd_kind("npx tsc --noEmit") == "build"
    assert ev.cmd_kind("git status --short") == "git"
    assert ev.cmd_kind("pip install psutil") == "install"
    assert ev.cmd_kind("py -3 tools/check.py C:/Users/me/secret") == "script"
    assert ev.cmd_kind("Get-Process") == "other"
    assert ev.validate("tool.call", {"tool": "ducky_terminal_run", "cmd": "pytest -q"}) == {"tool": "ducky_terminal_run"}


def test_new_failure_fields(monkeypatch) -> None:
    us.run_begin("r1", "c1", coding_agent="ducky")
    us.tool_done("ducky_terminal_run", ok=False, duration_ms=10, conv_id="c1", args={"command": "pytest -q"}, fail="exit_nonzero")
    us.tool_done("ducky_terminal_run", ok=True, duration_ms=10, conv_id="c1", args={"command": "pytest -q"})
    us.tool_done("web_fetch", ok=False, duration_ms=10, conv_id="c1", fail="timeout")
    us.tool_done("web_fetch", ok=False, duration_ms=10, conv_id="c2", fail="timeout")  # another chat: no after_fail
    us.stuck("claude_code", 130, tool="unreal__call_tool")
    us.run_end("r1", "done")
    rows, _ = us._take(100)
    calls = [r["props"] for r in rows if r["name"] == "tool.call"]
    assert calls[0]["cmd"] == "test" and "after_fail" not in calls[0]
    assert calls[1] == {"tool": "ducky_terminal_run", "ok": "yes", "duration": "1s", "cmd": "test", "after_fail": "yes"}
    assert "cmd" not in calls[2] and "after_fail" not in calls[3]
    stuck = next(r["props"] for r in rows if r["name"] == "agent.stuck")
    stuck.pop("v", None)
    assert stuck == {"provider": "claude", "after": "5m", "tool": "unreal__call_tool"}
    turn = next(r["props"] for r in rows if r["name"] == "chat.turn")
    assert turn["tool_calls"] == "1-5" and turn["tool_fails"] == "1-5", "the timeout counts, the non-zero exit does not"


def test_coding_agent_turn_counts_its_failed_calls() -> None:
    us.run_begin("r2", "c2", coding_agent="claude_code")
    us.turn_tool("r2")
    us.turn_tool("r2", failed=True)
    assert us._run_meta["r2"]["tools"] == 1 and us._run_meta["r2"]["fails"] == 1


def test_unclean_exit_carries_the_last_memory() -> None:
    assert ev.validate("error.code", {"code": "app_unclean_exit", "memory_mb": 700, "webview_mb": 2600}) == {
        "code": "app_unclean_exit", "memory": "1gb", "webview": "more"}
    mem = us._memory_now()
    assert mem.get("memory_mb", 1) > 0


def test_a_heartbeat_between_webview_scans_does_no_process_scan(monkeypatch) -> None:
    import sys
    import types

    scans: list[int] = []

    class Proc:
        def memory_info(self) -> Any:
            return types.SimpleNamespace(rss=700 * 1048576, private=900 * 1048576)

        def name(self) -> str:
            return "msedgewebview2.exe"

        def children(self, recursive: bool = False) -> list[Any]:
            scans.append(1)
            return [Proc()]

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=Proc))
    monkeypatch.setattr(us, "_webview_scan", None)
    assert us._memory_now() == {"memory_mb": 700, "webview_mb": 900} and len(scans) == 1
    assert us._memory_now() == {"memory_mb": 700, "webview_mb": 900} and len(scans) == 1, "inside the window: no scan"
    us._webview_scan = (time.monotonic() - us.WEBVIEW_SCAN_S - 1, 0)
    us._memory_now()
    assert len(scans) == 2


def test_the_sampler_never_names_itself_the_hot_thread(monkeypatch) -> None:
    import types

    monkeypatch.setattr(us, "_thread", types.SimpleNamespace(native_id=4242))
    assert us._hot_thread({4242: 0.0, 1: 0.0}, {4242: 0.5, 1: 0.1}) == "native"
    assert us._hot_thread({4242: 0.0}, {4242: 0.5}) == ""


def test_a_failed_tool_call_says_why(monkeypatch) -> None:
    # Oct 29 2026: a third of ducky_terminal_run calls counted as failed; most were a test's exit 1.
    from backend.bridge import shared_mcp

    assert ev.validate("tool.call", {"tool": "ducky_terminal_run", "ok": False, "fail": "exit_nonzero"})["fail"] == "exit_nonzero"
    assert "fail" not in ev.validate("tool.call", {"tool": "x", "ok": False, "fail": "Command failed (exit 1)"})
    seen: list[str] = []
    monkeypatch.setattr(us, "tool_done", lambda name, *, ok, fail="", **_: seen.append(fail))

    def call(text: str) -> None:
        result = {"content": [{"type": "text", "text": text}], "isError": False}
        shared_mcp._usage_tool_done("ducky_terminal_run", False, 0.0, None, {}, result)

    call('{"ok": false, "fail": "exit_nonzero", "exit_code": 1, "error": "Command failed (exit code 1)."}')
    call('{"ok": false, "error": "cannot complete a Verify leaf"}')
    call("ERROR: pack_id is required")
    call("Traceback (most recent call last)")
    assert seen == ["exit_nonzero", "refused", "refused", ""]
    monkeypatch.undo()
    us.tool_done("ducky_terminal_run", ok=False, duration_ms=4000, fail="exit_nonzero")
    us.tool_done("spawn_actor", ok=False, duration_ms=10)
    us.tool_done("spawn_actor", ok=True, duration_ms=10, fail="busy")
    rows, _ = us._take(100)
    calls = [r["props"] for r in rows if r["name"] == "tool.call"]
    assert [c.get("fail") for c in calls[-3:]] == ["exit_nonzero", "error", None]
    assert calls[-3] == {"tool": "ducky_terminal_run", "ok": "no", "duration": "10s", "fail": "exit_nonzero", "why": "exit_code"}


def test_a_new_pcs_first_open_is_sent_right_away(monkeypatch) -> None:
    """The install journey's "Opened UEFN Ducky": a PC closed or dead within its first minute
    was never heard from (the first timed send comes a minute in). Later opens wait for it."""
    from frontend import error_log

    meta: dict[str, str] = {}
    monkeypatch.setattr(us, "_meta_get", meta.get)
    monkeypatch.setattr(us, "_meta_set", meta.__setitem__)
    monkeypatch.setattr(us, "start", lambda: None)
    monkeypatch.setattr(error_log, "install_crash_hooks", lambda: None)
    sent: list[dict[str, Any]] = []
    us.send_override = lambda body: sent.append(body) or True

    us.app_opened()
    assert len(sent) == 1 and sent[0]["installId"] == meta[us.META_INSTALL]
    assert [e["name"] for e in sent[0]["events"]] == ["app.open"]
    us.app_closing()  # its own send, as before
    sent.clear()
    us.app_opened()   # this PC has its id now
    assert sent == []
    assert [r["name"] for r in us._take(100)[0]] == ["app.open"]
