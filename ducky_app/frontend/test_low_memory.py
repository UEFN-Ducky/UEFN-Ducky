"""A nearly full PC is named before Ducky dies of it (Oct 30 2026: 0.5 GB free at launch;
Nov 4 2026: 0.8-1.1 GB free and no warning before Ducky closed)."""

from __future__ import annotations

import os

import pytest

from backend.util import proc_table
from frontend import low_memory
from frontend.ui_web import admission

GB = 1024**3
ME = os.getpid()


def _proc(pid: int, ppid: int, name: str, gb: float) -> proc_table.Proc:
    started = 100.0 if pid == ME else 200.0  # Ducky started before everything it started
    return proc_table.Proc(pid, ppid, name, 0, int(gb * GB), 1, 1, 0.0, started)


def _pc_table() -> dict[int, proc_table.Proc]:
    rows = [
        _proc(ME, 1, "UEFN Ducky.exe", 0.6), _proc(9001, ME, "msedgewebview2.exe", 0.5),
        _proc(9002, ME, "claude.exe", 0.4), _proc(9003, 9002, "node.exe", 0.3),
        _proc(5, 1, "Memory Compression", 9.0), _proc(10, 1, "chrome.exe", 4.0), _proc(11, 1, "chrome.exe", 2.2),
        _proc(20, 1, "UnrealEditorFortnite.EXE", 5.1), _proc(30, 1, "Discord.exe", 0.9), _proc(40, 1, "node.exe", 0.2),
    ]
    return {p.pid: p for p in rows}


@pytest.fixture
def pc(monkeypatch):
    state = {"free": [2.0], "asked": [], "alerts": [], "events": [], "freed": [],
             "frees": {"freed_mb": 0.0, "did": []}}
    monkeypatch.delenv("DUCKY_SKIP_LOW_MEMORY_CHECK", raising=False)
    monkeypatch.setattr(admission, "free_memory_gb", lambda: state["free"].pop(0) if len(state["free"]) > 1 else state["free"][0])
    monkeypatch.setattr(proc_table, "table", _pc_table)
    monkeypatch.setattr("frontend.notifications.alert", lambda *a, **k: state["alerts"].append((a, k)) or True)
    monkeypatch.setattr("frontend.usage_stats.track", lambda name, **p: state["events"].append((name, p)) or True)
    monkeypatch.setattr(low_memory, "_count", lambda: None)
    monkeypatch.setattr(low_memory, "_last_alert", 0.0)
    monkeypatch.setattr(low_memory, "_last_warn", 0.0)
    monkeypatch.setattr(low_memory, "_last_freed", 0.0)
    monkeypatch.setattr(low_memory, "_freed_this_spell", False)
    monkeypatch.setattr("frontend.gpu_memory.read", lambda *a, **k: None)  # test_gpu_memory covers VRAM
    state["growth"] = None
    monkeypatch.setattr(low_memory, "_uefn_growth", lambda: state["growth"])
    monkeypatch.setattr("frontend.self_health.free_ducky_memory",
                        lambda beat=None: state["freed"].append(beat) or dict(state["frees"]))
    monkeypatch.setattr(low_memory, "_recent", low_memory.deque())
    clock = [1000.0]
    monkeypatch.setattr(low_memory.time, "monotonic", lambda: clock[0])
    state["clock"] = clock
    return state


def _asks(state, *answers):
    replies = list(answers)

    def ask(free, names):
        state["asked"].append((round(free, 1), names))
        return replies.pop(0)
    return ask


def _beat(state, free: float, total: float = 16.0, after_s: float = 30.0) -> None:
    state["clock"][0] += after_s
    low_memory.on_heartbeat({"free_gb": free, "total_gb": total})


NAMES = "chrome 6.2 GB, UnrealEditorFortnite 5.1 GB, UEFN Ducky + agents 1.8 GB"


def test_enough_memory_starts_without_asking(pc) -> None:
    assert low_memory.check_at_launch(_asks(pc)) is True and pc["asked"] == []


def test_a_full_pc_names_the_biggest_programs_and_try_again_checks_once_more(pc) -> None:
    pc["free"] = [0.5, 0.6, 3.0]
    assert low_memory.check_at_launch(_asks(pc, "try", "try")) is True
    assert pc["asked"] == [(0.5, NAMES), (0.6, NAMES)]


def test_continue_starts_anyway_and_cancel_does_not_start(pc) -> None:
    pc["free"] = [0.5]
    assert low_memory.check_at_launch(_asks(pc, "continue")) is True
    assert low_memory.check_at_launch(_asks(pc, "cancel")) is False


def test_ducky_and_everything_it_started_count_as_one_program(pc) -> None:
    # Ducky 0.6 + WebView2 0.5 + claude 0.4 + its node 0.3; the other node.exe stays apart.
    assert low_memory.biggest_programs(5) == NAMES + ", Discord 0.9 GB, node 0.2 GB"


def test_the_warning_level_is_1_5_gb_or_a_tenth_of_the_pc_whichever_is_higher() -> None:
    assert low_memory.warn_below_gb(16) == 1.6
    assert low_memory.warn_below_gb(8) == 1.5
    assert low_memory.warn_below_gb(None) == 1.5


def test_1_1_gb_free_on_16_gb_warns_and_says_to_save_in_uefn(pc) -> None:
    _beat(pc, 1.1)
    assert len(pc["alerts"]) == 1
    (title, status), extra = pc["alerts"][0]
    assert status == "Your PC is running low on RAM."
    assert extra["detail"].startswith("Low RAM: 1.1 GB of 16 GB free. Biggest users: " + NAMES + ".")
    assert NAMES in extra["detail"] and "Save your work in UEFN" in extra["detail"]
    assert pc["events"] == [("low_memory.warned", {"free": "under_1_5gb"})]


def test_3_gb_free_does_not_warn(pc) -> None:
    _beat(pc, 3.0)
    _beat(pc, 3.0)
    assert pc["alerts"] == [] and pc["events"] == []


def test_a_fast_drop_from_6_to_3_5_gb_within_a_minute_warns(pc) -> None:
    _beat(pc, 6.0)
    _beat(pc, 3.5, after_s=55)
    assert len(pc["alerts"]) == 1 and "falling fast" in pc["alerts"][0][1]["detail"]


def test_a_slow_drop_or_one_that_stays_above_4_gb_does_not_warn(pc) -> None:
    _beat(pc, 6.0)
    _beat(pc, 3.5, after_s=90)  # the 6 GB sample is older than DROP_WINDOW_S
    _beat(pc, 9.0, after_s=30)
    _beat(pc, 6.5, after_s=30)  # fell 2.5 GB but is still over DROP_UNDER_GB
    assert pc["alerts"] == []


def test_a_repeat_warning_within_ten_minutes_stays_quiet(pc) -> None:
    _beat(pc, 1.1)
    _beat(pc, 1.0, after_s=60)
    _beat(pc, 1.2, after_s=low_memory.WARN_EVERY_S - 120)
    assert len(pc["alerts"]) == 1
    _beat(pc, 1.2, after_s=120)
    assert len(pc["alerts"]) == 2


def test_critical_still_fires_inside_the_ten_minutes(pc) -> None:
    _beat(pc, 1.1)
    _beat(pc, 0.5, after_s=60)
    assert len(pc["alerts"]) == 2
    (_, status), extra = pc["alerts"][1]
    assert status == "Your PC is almost out of RAM." and "Save your work in UEFN now" in extra["detail"]
    assert pc["events"][-1] == ("low_memory.warned", {"free": "under_1gb"})


def test_a_running_ducky_raises_one_critical_alert_per_half_hour_while_the_pc_is_full(pc) -> None:
    _beat(pc, 2.5)
    _beat(pc, 0.4)
    _beat(pc, 0.3, after_s=60)
    assert len(pc["alerts"]) == 1
    (title, status), extra = pc["alerts"][0]
    assert status == "Your PC is almost out of RAM." and "chrome 6.2 GB" in extra["detail"]
    _beat(pc, 0.3, after_s=low_memory.RUN_EVERY_S)
    assert len(pc["alerts"]) == 2


def test_a_low_beat_frees_duckys_leftovers_once_per_spell(pc) -> None:
    _beat(pc, 1.1)
    _beat(pc, 1.0)  # same spell: not again
    assert len(pc["freed"]) == 1 and pc["freed"][0]["free_gb"] == 1.1
    _beat(pc, 3.0)  # the spell ends
    _beat(pc, 1.1)  # a new spell, but within five minutes of the last free
    assert len(pc["freed"]) == 1
    _beat(pc, 3.0)
    _beat(pc, 1.1, after_s=low_memory.FREE_EVERY_S)
    assert len(pc["freed"]) == 2


def test_freeing_enough_sends_no_alert(pc) -> None:
    # At 0.3 GB free Ducky stops its leftovers and the PC is at 1.7 GB: above the line.
    pc["frees"] = {"freed_mb": 1434.0, "did": ["2 leftover bridges", "1 abandoned sandbox program"]}
    pc["free"] = [1.7]
    _beat(pc, 0.3)
    assert len(pc["freed"]) == 1 and pc["alerts"] == [] and pc["events"] == []


def test_the_alert_says_what_ducky_already_freed(pc) -> None:
    pc["frees"] = {"freed_mb": 1434.0, "did": ["2 leftover bridges", "3 idle terminals"]}
    pc["free"] = [1.1]
    _beat(pc, 0.4)
    assert len(pc["alerts"]) == 1
    assert pc["alerts"][0][1]["detail"].startswith(
        "Low RAM: 1.1 GB of 16 GB free. Ducky already freed 1.4 GB (2 leftover bridges, 3 idle terminals). "
        "Biggest users: ")


def test_a_grown_uefn_on_top_adds_the_restart_line_and_a_fresh_one_does_not(pc) -> None:
    top = [{"name": "UnrealEditorFortnite", "gb": 10.8}, {"name": "chrome", "gb": 3.2},
           {"name": low_memory.DUCKY_LABEL, "gb": 0.9}]
    pc["growth"] = {"grown_gb": 6.2, "limit_gb": 4.0}
    text = low_memory.describe(0.6, 16, rows=top, freed={"freed_mb": 1434.0, "did": []})
    assert text == ("Low RAM: 0.6 GB of 16 GB free. Ducky already freed 1.4 GB. Biggest users: "
                    "UnrealEditorFortnite 10.8 GB, chrome 3.2 GB, UEFN Ducky + agents 0.9 GB. "
                    "UEFN grew 6.2 GB since the island loaded: save and restart UEFN to get it back.")
    pc["growth"] = {"grown_gb": 0.4, "limit_gb": 4.0}  # a fresh UEFN
    assert "restart UEFN" not in low_memory.describe(0.6, 16, rows=top)
    pc["growth"] = {"grown_gb": 6.2, "limit_gb": 4.0}  # grown, but chrome is the biggest user
    assert "restart UEFN" not in low_memory.describe(0.6, 16, rows=top[1:])


def test_the_usage_event_carries_only_the_free_memory_bucket() -> None:
    assert low_memory.free_bucket(0.3) == "under_0_5gb"
    assert low_memory.free_bucket(1.1) == "under_1_5gb"
    assert low_memory.free_bucket(3.9) == "under_4gb"
    assert low_memory.free_bucket(12) == "4gb_plus"
