from __future__ import annotations

from frontend import perf_trace, soak_budget


def _js(method: str, pid: int = 101) -> dict:
    return {"source": f"sess-{pid}", "payload": {"kind": "ui_js", "source": method, "js_bytes": 138}}


def _stall(ms: float, pid: int = 101) -> dict:
    return {"source": f"sess-{pid}", "payload": {"kind": "ui_stall", "duration_ms": ms}}


def test_flood_and_long_stall_are_reported():
    rows = [_js("browser_pane_set_bounds") for _ in range(4333)] + [_stall(9800.0)]
    broken = soak_budget.check_rows(rows, hours=1)
    assert "browser_pane_set_bounds: 4333 calls/h > 600" in broken
    assert any("worst ui_stall: 9800 ms" in b for b in broken)


def test_stall_count_budget():
    rows = [_stall(100.0) for _ in range(300)]
    broken = soak_budget.check_rows(rows, hours=2)
    assert any(b.startswith("ui_stalls: 150/h > 120") for b in broken)
    assert not any("worst" in b for b in broken)


def test_quiet_log_passes():
    rows = [_js("get_status") for _ in range(61)] + [_stall(300.0) for _ in range(10)]
    assert soak_budget.check_rows(rows, hours=1) == []


def test_rate_is_per_hour():
    rows = [_js("poll") for _ in range(1100)]  # 550/h over 2 h: within budget
    assert soak_budget.check_rows(rows, hours=2) == []


def test_cli_exit_codes(monkeypatch, capsys):
    class FakeEvents:
        rows: list = []

        def newest(self, kind, limit, since):
            assert kind == "perf"
            return self.rows

    fake = FakeEvents()
    monkeypatch.setattr(perf_trace, "_events", lambda: fake)
    fake.rows = [_js("plugin_team_overview") for _ in range(2000)]
    assert soak_budget.main(["--hours", "2"]) == 1
    assert "plugin_team_overview: 1000 calls/h > 600" in capsys.readouterr().out
    fake.rows = []
    assert soak_budget.main(["--hours", "2"]) == 0
    assert "ok" in capsys.readouterr().out


# --- memory budgets -------------------------------------------------------------------------

from backend.util.proc_table import Proc  # noqa: E402

MB = 1 << 20


def _proc(pid, ppid, name, mb, started=100.0):
    return Proc(pid, ppid, name, int(mb * MB), int(mb * MB), 10, 10, 0.0, started)


def _table(app=500, host=300, renderer=450, gpu=120, other_renderer=80):
    procs = {
        1: _proc(1, 0, "explorer.exe", 50, started=1.0),
        10: _proc(10, 1, "UEFN-Ducky.exe", app),
        11: _proc(11, 10, "UEFN-Ducky.exe", host),
        12: _proc(12, 10, "msedgewebview2.exe", 90),  # browser process
        13: _proc(13, 12, "msedgewebview2.exe", renderer),
        14: _proc(14, 12, "msedgewebview2.exe", gpu),
        15: _proc(15, 12, "msedgewebview2.exe", other_renderer),
        99: _proc(99, 1, "msedgewebview2.exe", 900),  # someone else's WebView2: not Ducky's
    }
    cmd = {
        11: ["UEFN-Ducky.exe", "agent-host", "--slot", "0"],
        12: ["msedgewebview2.exe", "--embedded-browser-webview=1"],
        13: ["msedgewebview2.exe", "--type=renderer", "--renderer-client-id=5"],
        14: ["msedgewebview2.exe", "--type=gpu-process"],
        15: ["msedgewebview2.exe", "--type=renderer", "--renderer-client-id=6"],
        99: ["msedgewebview2.exe", "--type=renderer"],
    }
    return procs, (lambda pid: cmd.get(pid, []))


def _samples(minutes, **rates):
    """One sample a minute; each role grows by ``rates[role]`` MB/h from its base."""
    base = {"app": 500.0, "agent_host": 300.0, "renderer": 450.0, "gpu": 120.0, "tree": 1500.0}
    out = []
    for m in range(minutes + 1):
        s = {"t": 1000.0 + m * 60}
        for k, v in base.items():
            s[k] = v + rates.get(k, 0.0) * m / 60
        out.append(s)
    return out


def test_role_of():
    assert soak_budget.role_of("UEFN-Ducky.exe", ["x", "agent-host", "--slot", "1"]) == "agent_host"
    assert soak_budget.role_of("msedgewebview2.exe", ["x", "--type=renderer"]) == "renderer"
    assert soak_budget.role_of("msedgewebview2.exe", ["x", "--type=gpu-process"]) == "gpu"
    assert soak_budget.role_of("msedgewebview2.exe", ["x", "--type=utility"]) == "other"
    assert soak_budget.role_of("UEFN-Ducky.exe", ["x", "bridge"]) == "other"


def test_memory_sample_by_tree_and_cmdline():
    procs, cmd = _table()
    s = soak_budget.memory_sample(procs, 10, cmd, now=5.0)
    assert s == {"t": 5.0, "app": 500.0, "agent_host": 300.0, "renderer": 450.0, "gpu": 120.0,
                 "tree": 500 + 300 + 90 + 450 + 120 + 80}


def test_memory_within_budget_passes():
    assert soak_budget.check_memory(_samples(60)) == []


def test_memory_over_budget():
    samples = _samples(60)
    samples[-1].update(renderer=912.0, app=600.0, agent_host=874.0, tree=2900.0)
    broken = soak_budget.check_memory(samples)
    assert "renderer: 912 MB > 500 MB budget" in broken
    assert "app: 600 MB > 550 MB budget" in broken
    assert "agent host: 874 MB > 350 MB budget" in broken
    assert "tree: 2900 MB > 1600 MB budget" in broken


def test_memory_growth_from_minute_10_to_60():
    broken = soak_budget.check_memory(_samples(60, app=40.0, tree=60.0))
    assert broken == ["tree growth: 60 MB/h > 50 MB/h (minute 10-60)"]


def test_growth_before_minute_10_is_ignored():
    samples = _samples(60)
    for s in samples[:10]:
        s["app"] = 200.0  # warm-up climb from 200 to 500 MB
    assert soak_budget.check_memory(samples) == []


def test_short_run_skips_growth_but_checks_budget():
    samples = _samples(12, tree=5000.0)
    assert soak_budget.check_memory(samples) == ["tree: 2500 MB > 1600 MB budget"]
    assert soak_budget.check_memory([]) == ["memory: no samples"]


def test_collect_writes_jsonl_and_stops_when_app_exits(tmp_path):
    procs, cmd = _table()
    tables = [procs, procs, {1: procs[1]}]
    out = tmp_path / "mem.jsonl"
    got = soak_budget.collect(10, minutes=10, every_s=60, out=out, table=lambda: tables.pop(0),
                              sleep=lambda s: None, cmdline_of=cmd)
    assert len(got) == 2
    assert soak_budget.load_samples(out) == got


def test_memory_cli_exit_codes(tmp_path, capsys):
    import json

    path = tmp_path / "mem.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in _samples(60)) + "\n", encoding="utf-8")
    assert soak_budget.main(["--memory", str(path)]) == 0
    assert "soak budgets ok" in capsys.readouterr().out
    bad = _samples(60)
    bad[-1]["renderer"] = 912.0
    path.write_text("\n".join(json.dumps(s) for s in bad) + "\n", encoding="utf-8")
    assert soak_budget.main(["--memory", str(path)]) == 1
    assert "renderer: 912 MB > 500 MB budget" in capsys.readouterr().out
