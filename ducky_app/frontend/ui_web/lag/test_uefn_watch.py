"""Ducky follows UEFN by itself. Nov 21 2026: the header stayed "Not checked" or "Offline"
beside a running UEFN until Restart UEFN listener was pressed (after every island switch and
every UEFN start), and 40 of 45 new PCs never read "Connected to UEFN"."""

from __future__ import annotations

import pytest

from frontend.ui_web.lag import uefn_watch


class _World:
    """UEFN, the last fetched status and the clock, as the watcher sees them."""

    def __init__(self) -> None:
        self.running = False
        self.status: dict | None = None
        self.next_status: dict = {}
        self.now = 0.0
        self.fetches = 0
        self.observed: list[dict] = []

    def refresh(self) -> None:
        self.fetches += 1
        self.status = dict(self.next_status)

    def watch(self) -> uefn_watch.UefnWatch:
        return uefn_watch.UefnWatch(
            running=lambda: self.running,
            last_status=lambda: self.status,
            refresh=self.refresh,
            observe=self.observed.append,
            now=lambda: self.now,
        )

    def run(self, watch: uefn_watch.UefnWatch, seconds: float) -> int:
        """Rounds every TICK_S for ``seconds``; how many status fetches they made."""
        before = self.fetches
        end = self.now + seconds
        while self.now < end:
            watch.tick()
            self.now += uefn_watch.TICK_S
        return self.fetches - before


OFFLINE = {"online": False, "epic_mcp_online": False}
ISLAND_OPEN_NO_LISTENER = {"online": False, "epic_mcp_online": True}
LINKED = {"online": True, "epic_mcp_online": True}


def test_with_uefn_closed_it_looks_once_and_then_never_again() -> None:
    world = _World()
    world.next_status = OFFLINE
    watch = world.watch()
    assert watch.tick() is True  # once: the header says Offline, not "Not checked"
    assert world.run(watch, 2 * 3600) == 0  # an idle Ducky with no UEFN fetches nothing
    assert world.fetches == 1


def test_uefn_opening_is_seen_and_the_link_shows_by_itself() -> None:
    world = _World()
    world.next_status = OFFLINE
    watch = world.watch()
    world.run(watch, 60)
    world.running = True  # the person starts UEFN; its island is still loading
    assert world.run(watch, 30) == 3  # at once, then every OFFLINE_EVERY_S
    world.next_status = LINKED  # the listener came up
    world.run(watch, 15)
    assert world.status == LINKED  # nobody pressed refresh
    assert world.run(watch, 120) == 4  # linked: one look every ONLINE_EVERY_S


def test_an_island_switch_that_drops_the_listener_is_seen_and_handed_to_the_restart() -> None:
    world = _World()
    world.running = True
    world.next_status = LINKED
    watch = world.watch()
    world.run(watch, 60)
    world.next_status = ISLAND_OPEN_NO_LISTENER  # the next island opened with Python off
    world.run(watch, uefn_watch.ONLINE_EVERY_S + uefn_watch.TICK_S)
    assert world.status == ISLAND_OPEN_NO_LISTENER
    world.observed.clear()
    assert world.run(watch, 60) == 6  # back to the short interval until it links again
    # Every round hands the status to the automatic restart, which counts the grace itself.
    assert world.observed and all(s == ISLAND_OPEN_NO_LISTENER for s in world.observed)


def test_uefn_closing_shows_offline_at_once_and_ends_the_restart_tries() -> None:
    world = _World()
    world.running = True
    world.next_status = LINKED
    watch = world.watch()
    world.run(watch, 30)
    world.running, world.next_status = False, OFFLINE
    world.observed.clear()
    assert watch.tick() is True and world.status == OFFLINE
    assert world.observed == [{}]  # the spell is over: the next UEFN start gets its own tries
    assert world.run(watch, 600) == 0


def test_the_watcher_and_the_restart_bring_a_listener_back_without_a_click(monkeypatch) -> None:
    """The whole path: UEFN open, island open, listener not started -> restarted after the grace."""
    from backend.bridge import revive

    revived: list[int] = []

    class _NowThread:
        def __init__(self, target, args, **_):
            self._go = lambda: target(*args)

        def start(self):
            self._go()

    world = _World()
    world.running = True
    world.next_status = ISLAND_OPEN_NO_LISTENER

    def bring_back(port: int) -> None:
        revived.append(port)
        world.next_status = LINKED  # Python turned on, the listener starts

    monkeypatch.setattr(revive.AutoRevive, "_run", staticmethod(bring_back))
    monkeypatch.setattr(revive.threading, "Thread", _NowThread)
    auto = revive.AutoRevive(grace_sec=60)
    watch = uefn_watch.UefnWatch(
        running=lambda: world.running, last_status=lambda: world.status, refresh=world.refresh,
        observe=lambda status: auto.observe(status, 4200, now=world.now), now=lambda: world.now,
    )
    world.run(watch, 55)
    assert revived == []  # a normal start gets its minute
    world.run(watch, 30)
    assert revived == [4200]
    world.run(watch, 15)
    assert world.status == LINKED
    world.run(watch, 600)
    assert revived == [4200]  # linked: nothing more to restart


def test_one_watcher_per_process(monkeypatch) -> None:
    started: list[str] = []

    class _Thread:
        def __init__(self, target, name, **_):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(uefn_watch, "_started", False)
    monkeypatch.setattr(uefn_watch.threading, "Thread", _Thread)
    api = object()
    assert uefn_watch.start(api) is True and uefn_watch.start(api) is False
    assert started == ["uefn-watch"]


@pytest.mark.parametrize("uefn_open, expected", [(True, "UEFN is open"), (False, "open UEFN")])
def test_the_offline_status_says_whether_uefn_is_open(monkeypatch, uefn_open, expected) -> None:
    from backend.bridge.status import ListenerStatusState, fetch_listener_status

    monkeypatch.setattr("backend.mcp_plugins.epic.probe_epic_mcp",
                        lambda **_k: {"epic_mcp_online": False, "epic_mcp_reason": "unreachable"})
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    monkeypatch.setattr("backend.bridge.editor_presence.uefn_running", lambda: uefn_open)
    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False and status["uefn_running"] is uefn_open
    assert status["status_text"].startswith("Offline") and expected in status["status_text"]
