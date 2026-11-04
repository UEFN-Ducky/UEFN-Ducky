"""The swarm soak's numbers from canned samples (no running app needed)."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("swarm_soak", Path(__file__).with_name("swarm_soak.py"))
soak = importlib.util.module_from_spec(_spec)
sys.modules["swarm_soak"] = soak  # dataclasses look their module up while the class is built
_spec.loader.exec_module(soak)


def _run(**kw):
    run = soak.Run(started=1000.0, ended=1000.0 + 600, agents=16)
    for i in range(10):
        run.samples.append(soak.Sample(t=1000.0 + i * 5, core_wait_p50=2.0 + i, core_wait_p95=10.0 * (i + 1),
                                       threads=500 + i * 10, agents_running=i + 6, app_cpu=float(i),
                                       webview_cpu=2.0, tool_cpu=1.0, free_gb=4.0 - i * 0.2))
    for i, seconds in enumerate([0.5, 1.0, 1.5, 2.0, 30.0]):
        run.opens.append(soak.ChatOpen(t=1000.0 + i, seconds=seconds))
    run.opens += [soak.ChatOpen(t=1100.0, seconds=60.0, timed_out=True)] * 2
    run.stub_events = ([{"kind": "turn_start", "session": f"s{i}"} for i in range(16)]
                       + [{"kind": "tool", "tool": "workspace_search"}] * 300
                       + [{"kind": "message", "text": "hi"}] * 3)
    run.messages_sent = 16
    for key, value in kw.items():
        setattr(run, key, value)
    return run


def test_budgets_name_each_miss_and_pass_when_met():
    good = {"agents": 16, "chat_open_s": {"p95": 1.2}, "threads_max": 150, "tool_servers_max": 5, "min_free_gb": 10.0}
    assert soak.budget_failures(good, free_before_gb=12.0) == []
    bad = {**good, "chat_open_s": {"p95": 4.2}, "threads_max": 539, "tool_servers_max": 17, "min_free_gb": 3.0}
    failures = soak.budget_failures(bad, free_before_gb=12.0)
    assert failures == [
        "p95_open 4.2 s > 1.5 s (16 agents)",
        "app_threads 539 > 200 (16 agents)",
        "tool_servers 17 > 5 (16 agents, 4 per host)",
        "memory_drop 9.0 GB > 4.0 GB (16 agents)",
    ]
    # Unknown measures are not failures.
    assert soak.budget_failures({"agents": 32}, free_before_gb=None) == []


def test_tool_servers_are_counted_per_sample():
    s = soak.summarize(_run(samples=[soak.Sample(t=1.0, tool_servers=3), soak.Sample(t=2.0, tool_servers=9)]))
    assert s["tool_servers_max"] == 9


def test_percentile_interpolates_and_handles_empty():
    assert soak.percentile([], 95) is None
    assert soak.percentile([5.0], 95) == 5.0
    assert soak.percentile([1, 2, 3, 4], 50) == 2.5
    assert soak.percentile([10, 20, 30, 40, 50], 95) == 48.0
    assert soak.percentile([3, 1, 2], 100) == 3


def test_summary_numbers():
    s = soak.summarize(_run(conv_save_p95_ms=123.456))
    assert s["minutes"] == 10.0 and s["agents"] == 16 and s["samples"] == 10
    assert s["core_wait_ms"]["p95"] == 95.5  # p95 of the per-sample p95s 10..100
    assert s["threads_max"] == 590
    assert s["min_free_gb"] == 2.2
    assert s["agents_running_max"] == 15
    assert s["cpu_pct_of_pc"]["app"] == {"avg": 4.5, "max": 9.0}
    assert s["conv_save_p95_ms"] == 123.5
    assert s["turns_started"] == 16 and s["tool_calls"] == 300 and s["tool_calls_per_min"] == 30.0
    assert s["messages_delivered"] == 3 and s["messages_sent"] == 16


def test_chat_opens_count_timeouts_apart_from_times():
    o = soak.summarize(_run())["chat_open_s"]
    assert o["count"] == 7 and o["timeouts"] == 2
    assert o["max"] == 30.0  # timed-out presses don't count as a time
    assert o["p50"] == 1.5


def test_an_empty_run_reports_nones_not_crashes():
    run = soak.Run(started=0.0, ended=60.0, agents=2)
    s = soak.summarize(run)
    assert s["chat_open_s"] == {"count": 0, "p50": None, "p95": None, "max": None, "timeouts": 0}
    assert s["min_free_gb"] is None and s["threads_max"] is None
    assert "Chat open timeouts" in soak.render_md(s)


def test_conv_save_is_found_in_a_nested_perf_report():
    perf = {"processes": [{"pid": 1, "timings": {"conv_save_ms": {"p50": 4, "p95": 88, "n": 40}}}]}
    assert soak.find_metric(perf, "conv_save") == {"p50": 4, "p95": 88, "n": 40}
    assert soak.find_metric({"x": 1}, "conv_save") is None


def test_results_md_shows_every_measure():
    md = soak.render_md(soak.summarize(_run()), {"PC before": {"free_gb": 9.1}})
    for label in ("Core-wait p50 / p95", "Threads max", "Chat open p50 / p95 / max", "conv_save p95",
                  "WebView2 CPU", "Tool servers CPU", "Min free memory", "Agents running at once",
                  "Tool calls / min", "Messages delivered", "## PC before"):
        assert label in md


def test_refuses_installed_app_and_real_appdata(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    installed = local / "Programs" / "UEFN-Ducky" / "UEFN-Ducky.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"")
    built = tmp_path / "dist" / "UEFN-Ducky.exe"
    built.parent.mkdir()
    built.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert "installed" in soak.refuse_reason(installed, tmp_path / "dsbx")
    assert "real AppData" in soak.refuse_reason(built, local / "UEFN-Ducky" / "box")
    assert "not found" in soak.refuse_reason(tmp_path / "nope.exe", tmp_path / "dsbx")
    assert soak.refuse_reason(built, tmp_path / "dsbx") == ""


def test_allows_exe_and_box_in_a_chat_sandbox(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    sandbox = local / "UEFN-Ducky" / "sandboxes" / "chat1"
    built = sandbox / "repo" / "dist" / "UEFN-Ducky.exe"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert soak.refuse_reason(built, sandbox / "soakbox") == ""
    assert "real AppData" in soak.refuse_reason(built, local / "UEFN-Ducky" / "box")


def _interrupt_run(lock=False):
    run = soak.Run(started=0.0, ended=600.0, agents=3, index_lock_left=lock)
    moments = ["during a 3-minute command", "during streaming text", "during an ask_user wait",
               "during an approval-card wait", "right after a turn ends"]
    for i in range(10):
        run.sends.append(soak.MessageSend(id=f"SOAKMSG-{i + 1}", moment=moments[i % 5], conv=f"c{i % 2}",
                                          sent_at=100.0 + i * 30))
    # 1-7 on time; 8 late (20 s); 9 delivered twice; 10 never arrives
    for i in range(7):
        run.stub_events.append({"kind": "message", "id": f"SOAKMSG-{i + 1}", "t": 100.0 + i * 30 + 3,
                                "path": "early return + steer" if i == 0 else "steer"})
    run.stub_events.append({"kind": "message", "id": "SOAKMSG-8", "t": 310.0 + 20, "path": "steer"})
    run.stub_events += [{"kind": "message", "id": "SOAKMSG-9", "t": 340.0 + 2, "path": "answer"},
                        {"kind": "message", "id": "SOAKMSG-9", "t": 340.0 + 9, "path": "turn"}]
    run.stops = [soak.StopPress(moment="during a git commit", conv="c2", pressed_at=500.0, stopped_at=516.0),
                 soak.StopPress(moment="during streaming text", conv="c2", pressed_at=560.0, stopped_at=562.5)]
    return run


def test_interrupt_table_counts_late_lost_and_duplicates():
    t = soak.interrupt_table(_interrupt_run())
    assert (t["sent"], t["on_time"], t["late"], t["lost"], t["duplicated"]) == (10, 8, 1, 1, 1)
    rows = {r["id"]: r for r in t["messages"]}
    assert rows["SOAKMSG-1"]["seconds"] == 3.0 and rows["SOAKMSG-1"]["path"] == "early return + steer"
    assert rows["SOAKMSG-8"]["late"] and rows["SOAKMSG-10"]["lost"] and rows["SOAKMSG-10"]["seconds"] is None
    assert rows["SOAKMSG-9"]["duplicate"] and rows["SOAKMSG-9"]["path"] == "answer + turn"
    assert [s["late"] for s in t["stops"]] == [True, False] and t["stops_late"] == 1
    assert not t["ok"]


def test_a_clean_interrupt_run_is_ok_and_a_left_lock_is_not():
    run = _interrupt_run()
    run.sends = run.sends[:7]
    run.stops = run.stops[1:]
    assert soak.interrupt_table(run)["ok"]
    run.index_lock_left = True
    assert not soak.interrupt_table(run)["ok"]
    run.stops.append(soak.StopPress(moment="x", conv="c2", pressed_at=1.0))  # never stopped
    assert soak.interrupt_table(run)["stops"][-1]["late"]


def test_interrupt_and_memory_tables_render():
    md = soak.render_interrupt_md(soak.interrupt_table(_interrupt_run(lock=True)))
    assert "| during a 3-minute command |" in md and "early return + steer" in md
    assert "LOST" in md and "LATE" in md and "DUPLICATE" in md and "index.lock left behind: YES" in md
    summary = soak.summarize(_run())
    mem = soak.memory_table(summary, 300, 12.5)
    assert mem == {"agents": 16, "memory_per_agent_mb": 300, "agents_running_max": 15, "min_free_gb": 2.2,
                   "free_before_gb": 12.5, "held_gb_max": 4.39}
    assert "| Agents running at once (max) | 15 |" in soak.render_memory_md(mem)


def test_panel_client_refuses_the_live_ports():
    for port in (4199, 4200):
        try:
            soak.Panel(port)
        except ValueError:
            continue
        raise AssertionError(f"Panel({port}) must refuse")
    assert soak.Panel(4399).base == "http://127.0.0.1:4399/__panel_api/"


def _seat_run():
    run = soak.Run(started=0.0, ended=900.0, agents=3, terminal_s=5.0)
    for i, (app, web, tool, comp, page) in enumerate([(300, 400, 200, 900, 2.0), (350, 500, 900, 950, 2.1),
                                                       (320, 450, 250, 920, 2.0)]):
        run.samples.append(soak.Sample(t=i * 5.0, app_mb=app, webview_mb=web, tool_mb=tool, memcomp_mb=comp,
                                       pagefile_gb=page, waiting=[2, 1, 0][i], listener_requests=0,
                                       free_gb=20.0, webview_cpu=10.0))
    run.stub_events = ([{"kind": "turn_start", "conv": c} for c in "abc"]
                       + [{"kind": "turn_end", "conv": c} for c in "abc"]
                       + [{"kind": "tool", "tool": "ducky_terminal_run", "seconds": s} for s in (5.2, 6.0, 7.5)]
                       + [{"kind": "tool", "tool": "workspace_read_file", "seconds": 40.0}])
    run.ui = {"ui_stalls": 4, "ui_stall_p95_ms": 120.0}
    return run


def test_seat_numbers_read_memory_waits_and_finished_seats():
    nums = soak.seat_numbers(_seat_run())
    assert nums["memory_mb"]["ducky_total"] == {"first": 900.0, "max": 1750.0, "last": 1020.0, "growth": 120.0}
    assert nums["memory_compression_mb"]["growth"] == 20.0
    assert nums["terminal_wait_s"]["max"] == 2.5  # 7.5 s minus the 5 s command; other tools don't count
    assert nums["waiting_max"] == 2 and nums["listener_requests"] == 0
    assert nums["seats_started"] == 3 and nums["seats_done"] == 3
    assert (nums["ui_stalls"], nums["ui_stall_p95_ms"]) == (4, 120.0)


def test_pass_bar_passes_a_clean_run_and_names_each_miss():
    summary = soak.summarize(_seat_run())
    summary["minutes"] = 15.0
    assert soak.bar_failures(summary) == []
    bad = {**summary, "ui_stalls": 30, "ui_stall_p95_ms": 450.0, "listener_requests": 7, "seats_done": 2,
           "cpu_pct_of_pc": {"webview2": {"avg": 31.0}}, "terminal_wait_s": {"max": 9.0}, "min_free_gb": 4.0,
           "memory_mb": {"ducky_total": {"growth": 640.0}},
           "memory_compression_mb": {"growth": 900.0}, "pagefile_gb": {"first": 2.0, "max": 5.5}}
    assert soak.bar_failures(bad) == [
        "ui_stalls 30 in 15.0 min (>= 20 per 15 min)",
        "ui_stall_p95 450.0 ms >= 200 ms",
        "webview2_cpu avg 31.0% >= 25%",
        "terminal_wait max 9.0 s > 5 s",
        "listener_requests 7 (UEFN closed: must be 0)",
        "seats_done 2 of 3",
        "ducky_memory_growth 640.0 MB >= 300 MB",
        "min_free 4.0 GB < 6 GB",
        "memory_compression climbed 900.0 MB > 500 MB",
        "pagefile spike 3.5 GB > 1 GB",
    ]
    squeezed = {**summary, "waiting_max": 0, "min_free_gb": 4.0}
    assert soak.bar_failures(squeezed, squeezed=True) == ["squeezed pass queued no seat (admission let every seat start)"]


def test_listener_counter_counts_connections():
    import socket
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    counter = soak.ListenerCounter((port,))
    try:
        for _ in range(3):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        deadline = time.monotonic() + 5
        while counter.count < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert counter.count == 3
    finally:
        counter.close()


def test_listener_counter_names_each_request_line():
    import socket
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    counter = soak.ListenerCounter((port,))
    try:
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
                conn.sendall(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
                time.sleep(0.1)
        deadline = time.monotonic() + 5
        while counter.count < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert counter.requests == {f":{port} GET /health HTTP/1.1": 2}
    finally:
        counter.close()


def test_log_tail_reads_only_new_whole_lines(tmp_path):
    log = tmp_path / "stub.jsonl"
    log.write_text('{"kind": "turn_start", "conv": "a"}\n{"kind": "tool"', encoding="utf-8")
    tail = soak.LogTail(log)
    tail.poll()
    assert tail.started == {"a"}
    with open(log, "a", encoding="utf-8") as fh:
        fh.write('}\n{"kind": "turn_start", "conv": "b"}\n')
    tail.poll()
    assert tail.started == {"a", "b"}


def test_port_base_moves_both_ports_and_spares_the_live_app(monkeypatch):
    import pytest
    monkeypatch.setattr(soak, "PANEL_PORT", 4400)
    monkeypatch.setattr(soak, "BRIDGE_PORT", 4401)
    soak.set_ports(4500)
    assert (soak.PANEL_PORT, soak.BRIDGE_PORT) == (4500, 4501)
    env = soak.sandbox_env(Path("box"), {})
    assert (env["UEFN_DUCKY_PANEL_PORT"], env["UEFN_DUCKY_PORT"]) == ("4500", "4501")
    with pytest.raises(SystemExit):
        soak.set_ports(4200)


def test_send_refused_reads_the_brokers_answers():
    assert soak.send_refused({"ok": True, "value": {"response_id": "", "queued_for": "c1"}}) == ""
    assert soak.send_refused({"ok": False, "error": "Unknown broker operation"}) == "Unknown broker operation"
    assert soak.send_refused({"ok": True, "value": {"not_sent": "repeat", "note": "Don't."}}).startswith(
        "not sent (repeat)")
    assert soak.send_refused(None) == ""


def test_remove_tree_clears_read_only_files(tmp_path):
    import os
    import stat
    obj = tmp_path / "box" / "repo" / ".git" / "objects" / "06" / "abc"
    obj.parent.mkdir(parents=True)
    obj.write_text("x", encoding="utf-8")
    os.chmod(obj, stat.S_IREAD)
    soak.remove_tree(tmp_path / "box")
    assert not (tmp_path / "box").exists()
