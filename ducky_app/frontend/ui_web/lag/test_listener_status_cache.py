"""The listener status is never fetched in the background: reads are free, only refresh fetches."""
import threading
import time

from frontend.ui_web.lag.listener_status_cache import ListenerStatusCache


def _slow_fetch(seconds, calls):
    def fetch():
        calls.append(time.monotonic())
        time.sleep(seconds)
        return {"online": True, "status_text": "Online", "n": len(calls)}

    return fetch


def _placeholder():
    return {"online": False, "status_text": "Not checked", "unchecked": True}


def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.01)
    return pred()


def test_reads_never_fetch():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder)
    for _ in range(1000):
        assert cache.get()["status_text"] == "Not checked"
    time.sleep(0.1)
    assert calls == []


def test_refresh_fetches_once_and_pushes_the_result():
    calls, pushed = [], []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder, on_result=pushed.append)
    cache.refresh()
    assert _wait_for(lambda: pushed)
    assert len(calls) == 1 and pushed[0]["status_text"] == "Online"
    assert cache.get()["status_text"] == "Online"
    time.sleep(0.1)
    assert len(calls) == 1  # no follow-up polling


def test_fifty_refreshes_during_a_3s_fetch_return_at_once_and_fetch_twice_at_most():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.5, calls), _placeholder)
    start = threading.Event()

    def caller():
        start.wait()
        cache.refresh()
        cache.get()

    threads = [threading.Thread(target=caller) for _ in range(50)]
    for t in threads:
        t.start()
    t0 = time.perf_counter()
    start.set()
    for t in threads:
        t.join()
    assert time.perf_counter() - t0 < 0.05
    assert _wait_for(lambda: not cache._inflight, timeout=5)
    assert 1 <= len(calls) <= 2  # one fetch, plus one queued by refreshes during it


def test_callers_get_copies():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder)
    cache.refresh()
    assert _wait_for(lambda: cache.value is not None)
    got = cache.get()
    got["mutated"] = True
    assert "mutated" not in cache.get()


def test_a_failing_fetch_does_not_wedge_the_cache():
    n = []

    def fetch():
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("UEFN busy")
        return {"online": True, "status_text": "Online"}

    cache = ListenerStatusCache(fetch, _placeholder)
    cache.refresh()
    assert _wait_for(lambda: cache.value is not None)
    assert cache.get()["status_text"] == "Offline"
    cache.refresh()
    assert _wait_for(lambda: cache.get()["status_text"] == "Online")


def test_idle_window_with_uefn_closed_makes_no_listener_fetches_in_10_minutes(monkeypatch):
    """The window reads the status on load and then only listens: 0 fetches (was 257 calls/2 h)."""
    from frontend.ui_web import panel_api

    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._listener_status_lock = threading.Lock()
    fetched = []
    monkeypatch.setattr(api, "_fetch_listener_status", lambda: fetched.append(1) or {"online": False}, raising=False)
    # Ten minutes of the old 8 s poll, read the new way: plain status reads.
    for _ in range(10 * 60 // 8):
        status = api.get_listener_status()
    time.sleep(0.1)
    assert fetched == []
    assert status["unchecked"] is True and status["online"] is False


def test_panel_api_refresh_does_not_block_on_a_slow_fetch(monkeypatch):
    from frontend.ui_web import panel_api

    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._listener_status_lock = threading.Lock()
    calls = []
    monkeypatch.setattr(api, "_refresh_listener_status", _slow_fetch(3.0, calls), raising=False)
    monkeypatch.setattr(api, "_push_panel", lambda event: None, raising=False)
    t0 = time.perf_counter()
    api.get_listener_status(refresh=True)
    assert time.perf_counter() - t0 < 0.05
    assert _wait_for(lambda: len(calls) == 1)
