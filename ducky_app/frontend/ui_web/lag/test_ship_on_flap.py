"""A flapping UEFN listener must not re-ship skills to every IDE on each flip."""
from frontend import ship_newest
from frontend.ui_web import panel_api
from frontend.ui_web.lag.listener_flap import OnlineDebouncer


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _poll_loop(monkeypatch, timeline):
    """Run panel_api's online check over (seconds, online) steps polled every second."""
    clock = Clock()
    monkeypatch.setattr(panel_api, "_listener_online", OnlineDebouncer(clock=clock))
    shipped = []
    monkeypatch.setattr(ship_newest, "ship_on_listener_online_async", lambda: shipped.append(clock.now))
    for seconds, online in timeline:
        for _ in range(seconds):
            panel_api.PanelApi._maybe_ship_on_listener_online(object(), {"online": online, "wedged": False})
            clock.now += 1.0
    return shipped


def test_ten_flaps_in_a_minute_ship_at_most_once(monkeypatch):
    # Nov 3 2026: a busy UEFN missed health pings; every offline -> online flip re-shipped.
    flaps = [(3, True), (3, False)] * 10  # 10 flaps in 60 s
    assert _poll_loop(monkeypatch, flaps) == []
    assert len(_poll_loop(monkeypatch, flaps + [(30, True)])) == 1


def test_online_must_hold_ten_seconds_and_counts_once_per_stretch(monkeypatch):
    shipped = _poll_loop(monkeypatch, [(9, True)])
    assert shipped == []
    shipped = _poll_loop(monkeypatch, [(60, True)])
    assert len(shipped) == 1
    shipped = _poll_loop(monkeypatch, [(15, True), (2, False), (15, True)])
    assert len(shipped) == 2


def test_wedged_listener_is_not_online():
    clock = Clock()
    deb = OnlineDebouncer(clock=clock)
    for _ in range(20):
        assert deb.observe(False) is False
        clock.now += 1


def test_listener_online_ship_skips_unless_the_listener_code_is_stale(monkeypatch):
    calls = []
    monkeypatch.setattr(ship_newest, "ship_newest_everywhere", lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(ship_newest, "listener_stamp_stale", lambda: False)
    ship_newest.ship_on_listener_online()
    monkeypatch.setattr(ship_newest, "listener_stamp_stale", lambda: True)
    ship_newest.ship_on_listener_online()
    assert [c["skip_if_recently_shipped"] for c in calls] == [True, False]


def test_recent_stamp_skips_the_full_ship(monkeypatch):
    import frontend.deploy as deploy

    monkeypatch.setattr(ship_newest, "maybe_bust_conversation_caches_for_app_version", lambda: False)
    monkeypatch.setattr(ship_newest, "_recent_ship_stamp", lambda: True)
    monkeypatch.setattr(deploy, "refresh_inits", lambda: [])
    synced = []
    monkeypatch.setattr(deploy, "sync_listener_to_appdata", lambda: synced.append(1))
    for _ in range(10):
        out = ship_newest.ship_newest_everywhere(skip_if_recently_shipped=True)
        assert "skipped" in out[0]
    assert synced == []
