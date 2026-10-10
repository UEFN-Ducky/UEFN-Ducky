"""The idle soak's verdict: what counts as a leak or needless work (no running app needed)."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("idle_soak", Path(__file__).with_name("idle_soak.py"))
soak = importlib.util.module_from_spec(_spec)
sys.modules["idle_soak"] = soak  # dataclasses look their module up while the class is built
_spec.loader.exec_module(soak)


def _samples(n=40, every=30.0, **grow):
    """n idle samples (20 minutes by default); grow maps a field to its change per sample."""
    out = []
    for i in range(n):
        out.append(soak.Sample(
            t=1000.0 + i * every, cpu_pct_of_pc=0.4, private_mb=400 + grow.get("private_mb", 0) * i,
            handles=1200 + int(grow.get("handles", 0) * i) + (40 if i % 2 else -40),  # real counts swing
            threads=90 + int(grow.get("threads", 0) * i), children=6 + int(grow.get("children", 0) * i),
            io_mb=50.0, hidden_gb=7 + grow.get("hidden_gb", 0) * i,
            python_threads=[f"{100 + (i if grow.get('reship') else 0)}:ship-newest", "1:MainThread"],
        ))
    return out


def _scans(n=10, every=120.0, rewrite=()):
    """AppData snapshots; files in ``rewrite`` change in every interval."""
    scans = []
    for i in range(n):
        files = {"ducky.db": (1, 4096), "skill_packs/ducky/SKILL.md": (1, 900)}
        for path in rewrite:
            files[path] = (i, 10)
        scans.append(soak.Scan(t=1000.0 + i * every, files=files))
    return scans


def _failed(verdict):
    return {c["check"].split(" (")[0] for c in verdict["checks"] if not c["ok"]}


def test_trend_helpers():
    assert round(soak.slope_per_hour([0, 360, 720], [0, 10, 20]), 6) == 100.0
    times = [i * 60.0 for i in range(30)]
    assert soak.growth_per_hour(times, [10.0 * i for i in range(30)]) == 600.0
    assert soak.growth_per_hour(times[:10], list(range(10))) is None  # 9 minutes: too short to judge


def test_a_quiet_app_passes():
    verdict = soak.analyse(_samples(), soak.Limits(), warmup_s=0, scans=_scans())
    assert verdict["ok"], verdict


def test_each_kind_of_leak_fails_its_own_check():
    limits = soak.Limits()
    assert _failed(soak.analyse(_samples(private_mb=1), limits, 0)) == {"private memory growth"}  # 120 MB/h
    assert _failed(soak.analyse(_samples(handles=3), limits, 0)) == {"handle growth"}  # 360/h
    assert _failed(soak.analyse(_samples(threads=0.5), limits, 0)) == {"thread growth"}
    assert _failed(soak.analyse(_samples(children=0.2), limits, 0)) == {"child process growth"}


def test_a_short_run_does_not_judge_trends():
    verdict = soak.analyse(_samples(n=20, every=15.0, handles=10), soak.Limits(), 0)
    skipped = {c["check"] for c in verdict["checks"] if c["skipped"]}
    assert {"private memory growth", "handle growth", "thread growth"} <= skipped
    assert verdict["ok"]


def test_busy_idle_cpu_fails():
    samples = _samples()
    for s in samples:
        s.cpu_pct_of_pc = 13.0  # the Oct 10 re-ship loop
    assert _failed(soak.analyse(samples, soak.Limits(), 0)) == {"idle CPU"}


def test_a_thread_started_again_and_again_fails():
    # How the re-ship loop showed up: a new "ship-newest" thread id every few seconds.
    verdict = soak.analyse(_samples(reship=True), soak.Limits(), 0)
    assert _failed(verdict) == {"repeated thread starts"}
    assert verdict["thread_restarts"]["ship-newest"] == 39


def test_a_ducky_file_rewritten_every_scan_fails_but_webview_files_do_not():
    # The same loop rewrote every skill pack's LICENSE.txt each time.
    verdict = soak.analyse(_samples(), soak.Limits(), 0, scans=_scans(rewrite=["skill_packs/ducky/LICENSE.txt"]))
    assert _failed(verdict) == {"Ducky file rewritten over and over"}
    assert verdict["ducky_files_changed"] == {"skill_packs/ducky/LICENSE.txt": 9}
    chromium = soak.analyse(_samples(), soak.Limits(), 0,
                            scans=_scans(rewrite=["webview/EBWebView/Default/settings_diagnostic.log"]))
    assert chromium["ok"] and chromium["webview_files_changed"]


def test_pc_memory_owned_by_no_program_only_warns():
    verdict = soak.analyse(_samples(hidden_gb=0.05), soak.Limits(), 0)  # 6 GB/h
    check = next(c for c in verdict["checks"] if c["check"].startswith("PC memory"))
    assert verdict["ok"] and check["warn"]


def test_start_up_work_inside_the_warm_up_is_not_judged():
    samples = _samples()
    for s in samples[:8]:
        s.cpu_pct_of_pc = 40.0  # start-up skill ship
    assert soak.analyse(samples, soak.Limits(), warmup_s=8 * 30)["ok"]
