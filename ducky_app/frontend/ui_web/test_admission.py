"""admission — member turns wait for memory, in order; people and top leads never wait."""

from __future__ import annotations

import threading
from collections import deque

import pytest

from frontend.ui_web import admission as adm


@pytest.fixture
def line(monkeypatch):
    free = {"gb": 2.5}
    roles: dict[str, str] = {}
    monkeypatch.setattr(adm, "_waiting", [])
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_admits", deque())
    monkeypatch.setattr(adm, "agent_cost_gb", lambda: 0.3)
    monkeypatch.setattr(adm, "min_free_gb", lambda: 6.0)
    monkeypatch.setattr(adm, "_read_free", lambda: free["gb"])
    monkeypatch.setattr(adm, "_role", lambda cid: roles.get(cid, "member"))
    monkeypatch.setattr(adm, "_ensure_thread", lambda: None)  # tests drive drain() themselves
    started: list[str] = []
    done = threading.Condition()

    def starter(cid):
        def go():
            with done:
                started.append(cid)
                done.notify_all()
        return go

    def wait_started(n):
        with done:
            assert done.wait_for(lambda: len(started) >= n, timeout=5)

    return {"free": free, "roles": roles, "started": started, "starter": starter, "wait": wait_started}


def _ask(line, cid, source="agent", pushed=None):
    return adm.may_start(cid, source, start=line["starter"](cid), push=(pushed.append if pushed is not None else None))


def test_low_memory_starts_two_members_and_lines_up_the_rest(line):
    pushed: list[dict] = []
    assert _ask(line, "m1") and _ask(line, "m2")
    assert not _ask(line, "m3", pushed=pushed)
    assert not _ask(line, "m4", pushed=pushed)
    assert not _ask(line, "m5", pushed=pushed)
    assert [w.conv_id for w in adm._waiting] == ["m3", "m4", "m5"]
    assert [e["text"] for e in pushed] == ["Waiting for memory (2.5 GB free) — 1st in line",
                                           "Waiting for memory (2.5 GB free) — 2nd in line",
                                           "Waiting for memory (2.5 GB free) — 3rd in line"]
    adm.release("m1")  # a turn ended: the floor lets exactly the oldest one in
    line["wait"](1)
    assert line["started"] == ["m3"]
    assert [w.conv_id for w in adm._waiting] == ["m4", "m5"]
    assert adm._waiting[0].shown == 1  # m4 moved up and was told so


def test_enough_memory_drains_the_line(line):
    assert _ask(line, "m1") and _ask(line, "m2")
    assert not _ask(line, "m3") and not _ask(line, "m4")
    line["free"]["gb"] = 12.0  # above the 6 GB floor, room for both
    assert adm.drain() == 2
    line["wait"](2)
    assert sorted(line["started"]) == ["m3", "m4"] and adm._waiting == []
    assert adm.holds("m3") and adm.holds("m4")


def test_a_person_send_never_waits(line):
    line["free"]["gb"] = 1.0
    assert _ask(line, "m1") and _ask(line, "m2")
    assert _ask(line, "m3", source="person")
    assert not adm.holds("m3")  # it takes no member slot either


def test_a_top_lead_never_waits_and_a_sub_lead_does(line):
    line["free"]["gb"] = 1.0
    line["roles"].update({"lead": "top_lead", "sub": "sub_lead", "solo": "solo"})
    assert _ask(line, "m1") and _ask(line, "m2")
    assert _ask(line, "lead")
    assert _ask(line, "solo")
    assert not _ask(line, "sub")
    assert [w.conv_id for w in adm._waiting] == ["sub"]


def test_stop_takes_a_chat_out_of_the_line(line):
    line["free"]["gb"] = 1.0
    assert _ask(line, "m1") and _ask(line, "m2")
    assert not _ask(line, "m3")
    adm.drop("m3")
    assert adm._waiting == []


def test_a_five_minute_wait_names_the_five_biggest_memory_users(line, monkeypatch):
    from backend.util import proc_table

    gb = 1024**3
    procs = [("UnrealEditorFortnite.exe", 24), ("claude.exe", 2), ("claude.exe", 1), ("msedgewebview2.exe", 3),
             ("python.exe", 2), ("Code.exe", 1), ("explorer.exe", 0)]
    table = {i: proc_table.Proc(i, 0, name, 0, size * gb, 1, 1, 0.0, 0.0) for i, (name, size) in enumerate(procs)}
    monkeypatch.setattr(proc_table, "table", lambda: table)
    clock = {"t": 1000.0}
    monkeypatch.setattr(adm, "_now", lambda: clock["t"])
    pushed: list[dict] = []
    line["free"]["gb"] = 1.0
    assert _ask(line, "m1") and _ask(line, "m2")
    assert not _ask(line, "m3", pushed=pushed)
    clock["t"] += adm.NOTICE_AFTER_S + 1
    adm.drain()
    notice = pushed[-1]
    assert [t["name"] for t in notice["top"]] == ["UnrealEditorFortnite.exe", "claude.exe", "msedgewebview2.exe",
                                                  "python.exe", "Code.exe"]
    assert "UnrealEditorFortnite.exe 24 GB" in notice["text"] and "claude.exe 3 GB" in notice["text"]
    adm.drain()
    assert pushed[-1] is notice  # told once


def test_free_memory_reads_something():
    assert adm.free_memory_gb() > 0
