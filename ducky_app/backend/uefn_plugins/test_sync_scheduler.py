"""The background sync of shared spaces, driven by a fake clock and a fake provider.

Writes come from another sqlite connection (their own thread), the way the MCP bridge
process writes: the scheduler only sees them through ``PRAGMA data_version``. Checks:
a round 10 s after the last write (60 s after the first of a stream) and at most one
per 30 s; a burst of writes or folder changes is one round; a round every 60 s while a
linked panel or the Workflows view is on screen, else every 15 minutes; failed rounds
retried after 1, 5, then 15 minutes, recorded on the space while the next tick still
runs; changes queued again without a write keep rounds coming while each sends some;
rounds of one space never overlap across processes; the exit flush is bounded; the
daemon never runs in the MCP bridge.

Team spaces (every plugin on a ledger, a fake team server): heads each minute while
active, every 15 minutes idle, and no provider round; an agent's write with the panel
closed makes the space active, sends presence and goes out as its call's one change; a
head event pulls within seconds; a writer waiting for a lock gets it within 3 s; the exit
flush sends without pulling and releases auto locks.
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import spaces, sync_scheduler
from backend.uefn_plugins import test_scopes
from backend.uefn_plugins.sync_scheduler import IDLE_EVERY_S, Scheduler

who = test_scopes.who

PLUGIN = "brainrot-tcg"
T = 1_000_000.0


class Recorder(spaces.Provider):
    """Rounds as they were asked for: ``(space, force)``. ``fail``: spaces whose round raises."""

    def __init__(self) -> None:
        self.rounds: list[tuple[str, bool]] = []
        self.fail: dict[str, Exception] = {}

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        self.rounds.append((space_id, force))
        if space_id in self.fail:
            raise self.fail[space_id]
        return {"state": "ok", "changed": [], "error": ""}


class Clock:
    def __init__(self) -> None:
        self.now = T

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The scheduler's clock, and the one rows are stamped with (``updated``)."""
    c = Clock()
    monkeypatch.setattr(repo, "time", SimpleNamespace(time=c))
    return c


@pytest.fixture
def provider() -> Recorder:
    impl = Recorder()
    spaces.install_for_tests(impl)
    yield impl
    spaces.reset_for_tests()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from frontend.ui_web import agent_modes

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_modes, "push_ui_event", seen.append)
    return seen


def scheduler(clock: Clock) -> Scheduler:
    return Scheduler(clock=clock, spawn=lambda fn: fn())  # rounds run inline


def elsewhere(fn: Any) -> None:
    """``fn`` on its own thread, so its own sqlite connection: like the MCP bridge."""
    box: dict[str, BaseException] = {}

    def run() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            box["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(10)
    if "error" in box:
        raise box["error"]


def link(aid: str, space: str, plugin: str = PLUGIN) -> None:
    repo.link_set(aid, plugin, space)
    repo.sync_put(aid, space, label=space, synced_at=1.0)


def write(aid: str, space: str, key: str, clock: Clock, at: float) -> None:
    clock.now = at
    elsewhere(lambda: repo.put(aid, space, PLUGIN, "doc", key, value="v", size=1, sha256=key, dirty=True))


def ticks(sched: Scheduler, clock: Clock, *at: float) -> list[list[str]]:
    out = []
    for when in at:
        clock.now = when
        out.append(sched.tick())
    return out


def test_a_write_elsewhere_starts_a_round_10s_after_the_last_write_at_most_every_30s(
    who, clock: Clock, provider: Recorder, events
) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"] and provider.rounds == [("spaceT", False)]  # the round at start

    write(aid, "spaceT", "card.a", clock, T + 100)
    assert ticks(sched, clock, T + 100, T + 109.9) == [[], []]
    assert ticks(sched, clock, T + 110) == [["spaceT"]]
    assert provider.rounds[-1] == ("spaceT", True)  # changes to send beat the provider's minute limit

    # Another write right after: 10 s later would be too soon, the 30 s cap holds it.
    write(aid, "spaceT", "card.b", clock, T + 111)
    assert ticks(sched, clock, T + 121, T + 139.9) == [[], []]
    assert ticks(sched, clock, T + 140) == [["spaceT"]]

    # Writes 3 s apart: one round, 10 s after the last of them.
    write(aid, "spaceT", "card.c", clock, T + 300)
    assert ticks(sched, clock, T + 300) == [[]]
    write(aid, "spaceT", "card.d", clock, T + 303)
    assert ticks(sched, clock, T + 305, T + 312.9, T + 313) == [[], [], ["spaceT"]]
    assert provider.rounds.count(("spaceT", True)) == 3
    assert events and events[-1] == {"type": "plugin_scope_changed", "plugins": [], "synced": True}


def test_rounds_every_60s_while_a_linked_panel_or_the_workflows_view_is_on_screen_else_every_15_min(
    who, clock: Clock, provider: Recorder, events
) -> None:
    from backend.uefn_plugins.panel_presence import note_panel

    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    repo.sync_put(aid, "spaceW", label="Workflows only", synced_at=1.0)  # a space with no linked plugin
    sched = scheduler(clock)
    assert ticks(sched, clock, T) == [["spaceT"]]
    assert ticks(sched, clock, T + 60, T + IDLE_EVERY_S - 1) == [[], []]
    assert ticks(sched, clock, T + IDLE_EVERY_S) == [["spaceT"]]
    assert provider.rounds[-1] == ("spaceT", False)  # a pull the provider may skip

    start = T + IDLE_EVERY_S
    clock.now = start + 1
    elsewhere(lambda: note_panel("main", f"plugin:{PLUGIN}:main", visible=True))
    assert ticks(sched, clock, start + 6, start + 59) == [[], []]
    assert ticks(sched, clock, start + 60, start + 119, start + 120) == [["spaceT"], [], ["spaceT"]]
    assert "spaceW" not in sched.spaces  # nothing of this account's lives there yet

    # The Workflows view on screen: every space this account keeps workflows in, each minute.
    clock.now = start + 121
    elsewhere(lambda: note_panel("main", "plugin:ducky.automations:workflows", visible=True))
    assert ticks(sched, clock, start + 126) == [["spaceW"]]
    assert ticks(sched, clock, start + 180, start + 185, start + 186) == [["spaceT"], [], ["spaceW"]]

    # Both off screen: back to every 15 minutes.
    clock.now = start + 187
    elsewhere(lambda: note_panel("main", f"plugin:{PLUGIN}:main", visible=False))
    elsewhere(lambda: note_panel("main", "plugin:ducky.automations:workflows", visible=False, open_=False))
    assert ticks(sched, clock, start + 192, start + 180 + IDLE_EVERY_S - 1) == [[], []]
    assert ticks(sched, clock, start + 180 + IDLE_EVERY_S) == [["spaceT"]]
    assert "spaceW" not in sched.spaces


def test_a_round_that_raises_is_recorded_and_retried_after_1_5_then_15_minutes(
    who, clock: Clock, provider: Recorder, events
) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    link(aid, "spaceU", plugin="forge")
    provider.fail["spaceT"] = KeyError("rev")
    sched = scheduler(clock)
    assert sorted(sched.tick()) == ["spaceT", "spaceU"]  # the other space still synced
    st = repo.sync_get(aid, "spaceT")
    assert st["state"] == "error" and "KeyError" in st["error"]
    assert repo.sync_get(aid, "spaceU")["state"] == "ok"

    def failing_rounds() -> list[float]:
        at = []
        for step in range(1, 2 * 3600):
            clock.now = T + step
            if "spaceT" in sched.tick():
                at.append(step)
        return at

    assert failing_rounds()[:5] == [60, 360, 1260, 2160, 3060]

    # It works again: back to the usual cadence, and the space says ok.
    provider.fail.clear()
    clock.now += 900
    assert "spaceT" in sched.tick()
    assert sched.spaces["spaceT"].fails == 0 and sched.spaces["spaceT"].state == "ok"


def test_a_round_that_worked_elsewhere_ends_the_backoff(who, clock: Clock, provider: Recorder, events) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    provider.fail["spaceT"] = KeyError("rev")
    sched = scheduler(clock)
    assert ticks(sched, clock, T, T + 61) == [["spaceT"], ["spaceT"]]
    assert sched.spaces["spaceT"].fails == 2  # next try in 5 minutes
    provider.fail.clear()
    clock.now = T + 100
    # The scope bar's round worked meanwhile (any process): the usual cadence again.
    elsewhere(lambda: repo.sync_put(aid, "spaceT", state="ok", error="", called_at=T + 100))
    assert ticks(sched, clock, T + 100, T + 400, T + 999) == [[], [], []]
    assert ticks(sched, clock, T + 1000) == [["spaceT"]]


def test_a_burst_of_writes_is_one_round_a_stream_waits_at_most_60s_and_sync_now_runs_at_once(
    who, clock: Clock, provider: Recorder, events
) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]

    # 20 writes in 2 s (dragging clips around): one round, 10 s after the last.
    for n in range(20):
        write(aid, "spaceT", f"card.{n}", clock, T + 100 + n * 0.1)
        sched.tick()
    started = [s for s in range(60) if ticks(sched, clock, T + 102 + s)[0]]
    assert started == [10] and provider.rounds.count(("spaceT", True)) == 1

    # A write every 3 s that never pauses: the batch still goes 60 s after its first write.
    rounds = []
    for s in range(121):
        if s % 3 == 0:
            write(aid, "spaceT", f"stream.{s}", clock, T + 500 + s)
        if ticks(sched, clock, T + 500 + s)[0]:
            rounds.append(s)
    assert rounds and 60 <= rounds[0] <= 66 and len(rounds) <= 2

    # Sync now mid-batch: its round runs at once, not at the batch's time.
    write(aid, "spaceT", "card.now", clock, T + 2000)
    sched.tick()
    before = len(provider.rounds)
    sync_scheduler.run_round(aid, "spaceT", force=True)
    assert provider.rounds[before:] == [("spaceT", True)]


def test_team_settings_are_clamped_and_the_scheduler_reads_the_send_delay(
    who, clock: Clock, provider: Recorder, events, monkeypatch: pytest.MonkeyPatch
) -> None:
    from frontend import settings

    assert sync_scheduler.clamp_team_settings(999, 5) == (60.0, 30.0)
    assert sync_scheduler.clamp_team_settings("x", None) == (sync_scheduler.DEBOUNCE_S, sync_scheduler.ACTIVE_EVERY_S)
    monkeypatch.setattr(settings.PanelSettings, "load",
                        classmethod(lambda cls, **kw: SimpleNamespace(team_send_delay_s=30, team_poll_s=120)))
    assert sync_scheduler.team_timing() == (30.0, 120.0)

    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"] and (sched.delay, sched.poll) == (30.0, 120.0)
    write(aid, "spaceT", "card.a", clock, T + 100)
    assert ticks(sched, clock, T + 100, T + 129.9, T + 130) == [[], [], ["spaceT"]]  # waits 30 s, not 10


def test_a_folder_change_waits_in_the_batch_instead_of_waking_a_round(
    who, clock: Clock, provider: Recorder, events
) -> None:
    """Before, a folder unit gone quiet woke a round within 5 s: each save its own send."""
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]
    clock.now = T + 100
    sched._folder_changed("spaceT", changing=True)  # still being edited
    clock.now = T + 105
    sched._folder_changed("spaceT", changing=True)
    clock.now = T + 108
    sched._folder_changed("spaceT", changing=False)  # quiet now: the batch's time stays
    assert ticks(sched, clock, T + 108, T + 114.9, T + 115) == [[], [], ["spaceT"]]
    assert provider.rounds[-1] == ("spaceT", True)


def test_a_space_failing_on_this_pc_retries_a_queued_write_on_the_backoff_not_every_30s(
    who, clock: Clock, provider: Recorder, events
) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    provider.fail["spaceT"] = OSError("offline")
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]
    write(aid, "spaceT", "card.a", clock, T + 10)
    assert ticks(sched, clock, T + 15, T + 40, T + 59) == [[], [], []]  # waits out the minute
    assert ticks(sched, clock, T + 60) == [["spaceT"]]
    assert provider.rounds[-1] == ("spaceT", True)


def test_no_provider_no_rounds_and_one_round_per_space_when_it_arrives(who, clock: Clock, events) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    sched = scheduler(clock)
    assert sched.tick() == [] and sched.spaces == {}
    impl = Recorder()
    spaces.install_for_tests(impl)
    try:
        clock.now += 1
        assert sched.tick() == ["spaceT"] and impl.rounds == [("spaceT", False)]
    finally:
        spaces.reset_for_tests()


def test_flush_sends_queued_changes_and_returns_within_its_timeout(who, monkeypatch, events) -> None:
    aid = who.be("ana@x.org")
    link(aid, "spaceT")
    link(aid, "spaceU", plugin="forge")
    repo.put(aid, "spaceT", PLUGIN, "doc", "card.a", value="v", size=1, sha256="a", dirty=True)
    gate = threading.Event()

    class Hung(Recorder):
        def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
            self.rounds.append((space_id, force))
            gate.wait(30)
            return {"state": "ok", "changed": [], "error": ""}

    impl = Hung()
    spaces.install_for_tests(impl)
    try:
        began = time.monotonic()
        assert sync_scheduler.flush(timeout=1.0) == 1
        assert time.monotonic() - began < 3.0
        assert impl.rounds == [("spaceT", True)]  # only the space with something to send
    finally:
        gate.set()
        spaces.reset_for_tests()
    assert sync_scheduler.flush(timeout=1.0) == 0  # no provider: nothing to do


def test_hard_exit_flushes_for_5s_before_plugins_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import boot_report
    from frontend.ui_web import shutdown

    order: list[str] = []
    monkeypatch.setattr(shutdown, "_exit_started", False)
    monkeypatch.setattr(boot_report, "mark", lambda *a, **k: None)
    monkeypatch.setattr(sync_scheduler, "flush", lambda timeout=5.0: order.append(f"flush {timeout:g}") or 0)
    for name in ("_stop_all_agents", "_stop_cpu_workers", "_stop_all_terminals", "_stop_mcp_plugins",
                 "release_panel_process", "_kill_sibling_processes", "_spawn_kill_uefn_processes",
                 "_terminate_process"):
        monkeypatch.setattr(shutdown, name, lambda name=name: order.append(name))
    shutdown.hard_exit()
    assert order.index("flush 5") == order.index("_stop_mcp_plugins") - 1
    assert order[-1] == "_terminate_process"


def test_never_in_the_mcp_bridge(monkeypatch: pytest.MonkeyPatch, who) -> None:
    monkeypatch.setenv("UEFN_DUCKY_MCP_BRIDGE", "1")
    impl = Recorder()
    spaces.install_for_tests(impl)
    try:
        aid = who.be("ana@x.org")
        repo.put(aid, "spaceT", PLUGIN, "doc", "card.a", value="v", size=1, sha256="a", dirty=True)
        assert sync_scheduler.start() is False
        assert sync_scheduler.flush(timeout=1.0) == 0 and impl.rounds == []
    finally:
        spaces.reset_for_tests()


class Batches(spaces.Provider):
    """Sends at most two queued changes per round (a provider's batch), stamping each round
    as a real one does; never sends the ``stuck`` keys."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.rounds: list[tuple[str, bool]] = []
        self.stuck: set[str] = set()

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        self.rounds.append((space_id, force))
        repo.sync_put(account, space_id, called_at=self.clock())
        for row in [r for r in repo.dirty(account, space_id, 100) if r["key"] not in self.stuck][:2]:
            repo.mark_pushed(account, space_id, row["plugin_id"], row["kind"], row["key"], rev=1, sha256=row["sha256"])
        return {"state": "ok", "changed": [], "error": ""}


def test_changes_queued_again_keep_rounds_coming_while_each_sends_some(who, clock: Clock, events) -> None:
    impl = Batches(clock)
    spaces.install_for_tests(impl)
    try:
        aid = who.be("ana@x.org")
        link(aid, "spaceT")
        clock.now = T - 3600
        for key in ("a", "b", "c", "d", "e"):
            repo.put(aid, "spaceT", PLUGIN, "doc", key, value="v", size=1, sha256=key, dirty=False, rev=3)
        sched = scheduler(clock)
        assert ticks(sched, clock, T) == [["spaceT"]]  # the round at start: nothing to send
        # Upload my copy queues all five again with no new write, and its own round sends two.
        clock.now = T + 100
        elsewhere(lambda: repo.settle_reset(aid, "spaceT", keep=True))
        elsewhere(lambda: sync_scheduler.run_round(aid, "spaceT", force=True))
        assert repo.count_dirty(aid, "spaceT") == 3
        # Before, the rest went one batch per 15-minute round. Now the next comes 30 s after.
        assert ticks(sched, clock, T + 105, T + 129.9, T + 130) == [[], [], ["spaceT"]]
        assert impl.rounds[-1] == ("spaceT", True) and repo.count_dirty(aid, "spaceT") == 1
        assert ticks(sched, clock, T + 136, T + 159.9, T + 160) == [[], [], ["spaceT"]]
        assert repo.count_dirty(aid, "spaceT") == 0
        assert ticks(sched, clock, T + 166, T + 300) == [[], []]  # all sent: back to the cadence

        # A change the provider keeps skipping: its round sent nothing, so no round follows.
        impl.stuck.add("z")
        write(aid, "spaceT", "z", clock, T + 400)
        assert ticks(sched, clock, T + 410) == [["spaceT"]]
        assert ticks(sched, clock, T + 411, T + 435, T + 500, T + 900) == [[], [], [], []]
        assert repo.count_dirty(aid, "spaceT") == 1
    finally:
        spaces.reset_for_tests()


# --------------------------------------------------------------------------- team spaces (a ledger)

dpapi = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
TEAM = "cards"


class Team(spaces.Provider):
    """Every plugin is on the new sync: its ledger is Ana's client of the fake server.
    ``rounds``: the provider's own rounds (none while every plugin has a ledger)."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.rounds: list[tuple[str, bool]] = []

    def enabled(self) -> bool:
        return True

    def ledger(self, account: str, space_id: str, plugin: str) -> Any:
        return self.backend

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        self.rounds.append((space_id, force))
        return {"state": "ok", "changed": [], "error": ""}


@pytest.fixture
def team(monkeypatch: pytest.MonkeyPatch):
    """Ana signed in, ``cards`` linked to spaceT, a fake team server, a clock that starts
    now (rows and activity are stamped with it), and Ben's client."""
    import hashlib

    from backend.store.repos import space_vcs
    from backend.uefn_plugins import data_crypto, vcs_engine
    from backend.uefn_plugins import ledger_conformance as conf
    from frontend import duckyos_account

    key = "https://uefnducky.org|ana@example.com"
    data_crypto.reset_for_tests()
    monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: key)
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: hashlib.sha256(b"adk/" + key.encode()).digest())
    clock = Clock()
    clock.now = time.time()
    monkeypatch.setattr(repo, "time", SimpleNamespace(time=clock))
    monkeypatch.setattr(space_vcs, "time", SimpleNamespace(time=clock))
    from backend.uefn_plugins import scopes

    aid = scopes.account_id()
    repo.link_set(aid, TEAM, "spaceT")
    repo.sync_put(aid, "spaceT", label="Alpha", synced_at=1.0)
    world = conf.FakeWorld()
    impl = Team(world.server.client("u_ana", vcs_engine.device_id(), name="Ana"))
    spaces.install_for_tests(impl)
    ben = world.server.client("u_ben", "d_ben", name="Ben")
    yield SimpleNamespace(aid=aid, clock=clock, world=world, provider=impl, ben=ben)
    spaces.reset_for_tests()
    scopes.forget_change_listeners(TEAM)
    world.server.rmtree()
    data_crypto.reset_for_tests()


def _heads(team: Any) -> int:
    return sum(1 for method, *_ in team.world.server.calls if method == "heads")


@dpapi
def test_a_team_space_asks_heads_every_minute_while_active_and_every_15_minutes_idle(team, events) -> None:
    from backend.uefn_plugins.panel_presence import note_panel

    clock, start = team.clock, team.clock.now
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"] and _heads(team) == 1  # heads at start
    assert team.provider.rounds == []  # every plugin is on the new sync: no provider round
    assert sched.spaces["spaceT"].team == frozenset({TEAM}) and not sched.spaces["spaceT"].hot
    assert ticks(sched, clock, start + 60, start + 300, start + IDLE_EVERY_S - 1) == [[], [], []]
    # (+1: the round at start stamped the real clock a moment after it began)
    assert ticks(sched, clock, start + IDLE_EVERY_S + 1) == [["spaceT"]] and _heads(team) == 2
    assert team.ben.beat("spaceT", {}) and team.world.server.space("spaceT").presence.keys() == {("u_ben", "d_ben")}

    # The plugin's panel on screen: active, a round (heads) each minute.
    later = start + IDLE_EVERY_S + 1
    clock.now = later + 1
    elsewhere(lambda: note_panel("main", f"plugin:{TEAM}:main", visible=True))
    assert ticks(sched, clock, later + 6, later + 59, later + 60, later + 119, later + 120) == [
        [], [], ["spaceT"], [], ["spaceT"]]
    assert sched.spaces["spaceT"].hot and _heads(team) == 4
    from backend.uefn_plugins import sync_scheduler as module

    module._SCHEDULER = sched
    try:
        assert spaces.space_active(team.aid, "spaceT")
    finally:
        module._SCHEDULER = None
    clock.now = later + 121
    elsewhere(lambda: note_panel("main", f"plugin:{TEAM}:main", visible=False))
    assert ticks(sched, clock, later + 127, later + 180, later + 120 + IDLE_EVERY_S - 1) == [[], [], []]
    assert not spaces.space_active(team.aid, "spaceT")


@dpapi
def test_an_agent_write_with_the_panel_closed_makes_the_space_active_sends_a_beat_and_pushes(team, events) -> None:
    from backend.uefn_plugins import vcs_engine
    from backend.uefn_plugins.scopes import PluginData, write_group

    clock, start = team.clock, team.clock.now
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]  # the round at start: on the new sync from now on
    assert not sched.spaces["spaceT"].hot

    def agent_edit() -> None:
        with write_group(TEAM, actor="agent", ducky="Builder", message="cards_tune"):
            PluginData(TEAM).put("card.pip", {"hp": 9})
            PluginData(TEAM).put("card.zed", {"hp": 1})

    clock.now = start + 100
    elsewhere(agent_edit)  # the MCP bridge's own connection
    assert ticks(sched, clock, start + 105) == [["spaceT"]]  # a round 5 s after the call returned
    rec = sched.spaces["spaceT"]
    assert rec.hot and not rec.active
    blob, _ = team.world.server.space("spaceT").presence[("u_ana", vcs_engine.device_id())]
    editing = blob["plugins"][TEAM]["editing"]
    assert {(e["unitName"], e["actor"], e["ducky"]) for e in editing} == {
        ("card.pip", "agent", "Builder"), ("card.zed", "agent", "Builder")}
    repo_ = team.world.server.space("spaceT").repo(TEAM)
    assert repo_.head == 1 and repo_.changesets[1].message == "cards_tune" and len(repo_.changesets[1].items) == 2
    assert spaces.space_active(team.aid, "spaceT")  # without a running scheduler: its own look
    # Active: a round each minute. Five quiet minutes after the write it is idle again:
    # heads every 15 minutes, no beats.
    assert ticks(sched, clock, start + 165, start + 225) == [["spaceT"], ["spaceT"]]
    beats = sum(1 for method, *_ in team.world.server.calls if method == "beat")
    assert ticks(sched, clock, start + 401, start + 470, start + 900) == [[], [], []]
    assert not sched.spaces["spaceT"].hot
    assert sum(1 for method, *_ in team.world.server.calls if method == "beat") == beats


@dpapi
def test_a_live_space_with_its_panel_hidden_keeps_beating(team, events, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live with the panel off screen and nothing written: presence still goes out and a
    round comes each minute, or the presence lease runs out and the group drops the links."""
    from backend.uefn_plugins import live, vcs_engine

    clock, start = team.clock, team.clock.now
    monkeypatch.setattr(live, "spaces_live", lambda: {(team.aid, "spaceT")})
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]
    rec = sched.spaces["spaceT"]
    assert rec.live and not rec.hot and not rec.active
    assert ("u_ana", vcs_engine.device_id()) in team.world.server.space("spaceT").presence
    assert ticks(sched, clock, start + 30, start + 61) == [[], ["spaceT"]]  # active rounds, not 15 minutes


@dpapi
def test_a_held_control_goes_out_in_presence_with_the_panel_hidden_and_not_live(team, events) -> None:
    """A teammate whose page opens later greys a control out from this PC's presence: a PC
    that holds one keeps beating even when nothing else makes the space in use."""
    from backend.uefn_plugins import live, vcs_engine

    sched = scheduler(team.clock)
    assert sched.tick() == ["spaceT"]
    assert not sched.spaces["spaceT"].hot and not sched.spaces["spaceT"].live
    try:
        assert live.hold(TEAM, "e2e-probe-control", True)["ok"]
        sched.tick(team.clock.now + 61)
        blob, _ = team.world.server.space("spaceT").presence[("u_ana", vcs_engine.device_id())]
        assert blob["plugins"][TEAM]["holds"] == [{"a": "e2e-probe-control", "t": 0.0, "v": ""}]
    finally:
        live.reset_for_tests()


@dpapi
def test_a_head_event_pulls_within_seconds(team, events) -> None:
    from backend.uefn_plugins import ledger_conformance as conf
    from backend.uefn_plugins.scopes import PluginData

    clock, start = team.clock, team.clock.now
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]
    out = team.ben.push("spaceT", TEAM, conf.changeset(conf.put_item("doc", "card.new", b'{"v":1}')))
    assert type(out).__name__ == "Committed"
    spaces.remote_event(team.aid, "spaceT", "head", TEAM, {"seq": out.seq})
    # At most one round per 5 s (+1: the round at start stamped the real clock a moment later).
    assert ticks(sched, clock, start + 1, start + 6) == [[], ["spaceT"]]
    assert PluginData(TEAM).get("card.new") == {"v": 1}
    # Open panels hear which keys came in, so they merge them and keep what is open.
    assert events[-1] == {"type": "plugin_scope_changed", "plugins": [TEAM], "synced": True,
                          "keys": {TEAM: ["card.new"]}}


@dpapi
def test_a_pull_asked_for_while_another_round_holds_the_space_runs_seconds_later(
        team, events, monkeypatch: pytest.MonkeyPatch) -> None:
    """A teammate's commit note (or the Store's head event) wakes a pull; if a round already
    holds the space (the bar's own sync, a folder look), the pull waits a few seconds instead
    of being dropped until the next minute's round."""
    from backend.uefn_plugins import ledger_conformance as conf
    from backend.uefn_plugins import sync_scheduler as module
    from backend.uefn_plugins.scopes import PluginData

    clock, start = team.clock, team.clock.now
    sched = scheduler(clock)
    assert sched.tick() == ["spaceT"]
    out = team.ben.push("spaceT", TEAM, conf.changeset(conf.put_item("doc", "card.new", b'{"v":1}')))
    spaces.remote_event(team.aid, "spaceT", "head", TEAM, {"seq": out.seq})
    real, calls = module.run_round, []

    def busy_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs.get("pull"))
        if len(calls) == 1:
            return {"state": "busy", "changed": [], "error": ""}
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "run_round", busy_once)
    assert ticks(sched, clock, start + 6) == [["spaceT"]] and PluginData(TEAM).get("card.new") is None  # busy
    assert ticks(sched, clock, start + 12) == [["spaceT"]]  # the wake came back: due again 5 s on
    assert PluginData(TEAM).get("card.new") == {"v": 1}
    assert calls[1] == calls[0] == frozenset({TEAM})  # the same pull, asked for again


@dpapi
def test_a_lock_request_is_answered_within_3s(team, events, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from backend.store.repos import space_vcs
    from backend.uefn_plugins import store
    from backend.uefn_plugins.scopes import PluginData

    monkeypatch.setattr(repo, "time", time)  # real time: the daemon's own loop
    monkeypatch.setattr(space_vcs, "time", time)
    folder = store.plugin_dir(TEAM)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps({"id": TEAM, "data_locks": {"scene.": "required"}}))
    sched = Scheduler()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            sched.tick()
            stop.wait(0.2)

    daemon = threading.Thread(target=loop)
    daemon.start()
    try:
        deadline = time.monotonic() + 10
        while space_vcs.head_get(team.aid, "spaceT", TEAM)["engine"] != "v2" and time.monotonic() < deadline:
            time.sleep(0.05)  # the round at start puts the plugin on the new sync
        began = time.monotonic()
        PluginData(TEAM).put("scene.duel", {"v": 1})
        assert time.monotonic() - began < 3.0
    finally:
        stop.set()
        daemon.join(10)
        (folder / "plugin.json").unlink(missing_ok=True)
    assert PluginData(TEAM).get("scene.duel") == {"v": 1}
    held = [(lk["unit"], lk["mine"], lk["reason"]) for lk in space_vcs.locks(team.aid, "spaceT", TEAM)]
    assert held == [("doc:scene.duel", True, "auto")]


@dpapi
def test_exit_sends_without_pulling_and_releases_auto_locks(team, events) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import ledger_conformance as conf, vcs_engine
    from backend.uefn_plugins.scopes import PluginData

    assert sync_scheduler.run_round(team.aid, "spaceT", force=True)["state"] == "ok"
    PluginData(TEAM).put("card.pip", {"hp": 2})
    vcs_engine.acquire(team.aid, "spaceT", TEAM, team.provider.backend, [("doc:card.pip", "card.pip")], reason="auto")
    vcs_engine.acquire(team.aid, "spaceT", TEAM, team.provider.backend, [("doc:card.keep", "card.keep")])
    team.ben.push("spaceT", TEAM, conf.changeset(conf.put_item("doc", "card.ben", b"{}")))
    calls = len(team.world.server.calls)
    assert sync_scheduler.flush(timeout=5.0) == 1
    made = [m for m, *_ in team.world.server.calls[calls:]]
    assert "push" in made and "pull" not in made and "heads" not in made
    assert team.world.server.space("spaceT").repo(TEAM).items[("doc", "card.pip")].content == b'{"hp":2}'
    assert PluginData(TEAM).get("card.ben") is None  # nothing came down
    assert [lk["unit"] for lk in space_vcs.locks(team.aid, "spaceT", TEAM) if lk["mine"]] == ["doc:card.keep"]
    assert ("cards", "doc:card.pip") not in team.world.server.space("spaceT").locks  # released on the server too


def test_rounds_of_one_space_never_overlap_across_processes(who, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows byte-range locks hold per handle, so another thread's handle stands in for the
    # MCP bridge process here.
    impl = Recorder()
    spaces.install_for_tests(impl)
    held, release = threading.Event(), threading.Event()

    def bridge_round() -> None:
        with sync_scheduler._round_lock("acc", "spaceT", 0) as got:
            assert got
            held.set()
            release.wait(10)

    worker = threading.Thread(target=bridge_round)
    worker.start()
    try:
        assert held.wait(5)
        assert sync_scheduler.run_round("acc", "spaceT", force=False) == {"state": "busy", "changed": [], "error": ""}
        assert sync_scheduler.run_round("acc", "spaceU", force=False)["state"] == "ok"  # other spaces go on
        monkeypatch.setattr(sync_scheduler, "ROUND_WAIT_S", 0.5)
        assert sync_scheduler.run_round("acc", "spaceT", force=True)["state"] == "busy"  # waited, then gave up
        assert impl.rounds == [("spaceU", False)]
        monkeypatch.setattr(sync_scheduler, "ROUND_WAIT_S", 10.0)
        threading.Timer(0.3, release.set).start()
        assert sync_scheduler.run_round("acc", "spaceT", force=True)["state"] == "ok"  # ran once it was free
        assert impl.rounds[-1] == ("spaceT", True)
    finally:
        release.set()
        worker.join(10)
        spaces.reset_for_tests()
