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
