"""get_listener_status answers from a cache: a slow UEFN never stalls the window."""
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
    return {"online": False, "status_text": "Checking…"}


def _wait_for(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.01)
    return pred()


def test_fifty_concurrent_calls_with_a_3s_fetch_return_at_once_and_fetch_once():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(3.0, calls), _placeholder)
    results, start = [], threading.Event()

    def caller():
        start.wait()
        results.append(cache.get())

    threads = [threading.Thread(target=caller) for _ in range(50)]
    for t in threads:
        t.start()
    t0 = time.perf_counter()
    start.set()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"50 calls took {elapsed * 1000:.0f} ms"
    assert len(results) == 50 and all(r["status_text"] == "Checking…" for r in results)
    time.sleep(0.2)
    assert len(calls) == 1


def test_fresh_value_is_served_without_fetching_and_stale_value_refetches_once():
    clock = [0.0]
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder, max_age_s=2.0, clock=lambda: clock[0])
    cache.get()
    assert _wait_for(lambda: cache.value is not None)
    for _ in range(100):
        assert cache.get()["status_text"] == "Online"  # fresh: no fetch
    time.sleep(0.05)
    assert len(calls) == 1
    clock[0] = 5.0  # stale now
    for _ in range(20):
        cache.get()
    assert _wait_for(lambda: len(calls) == 2)
    time.sleep(0.05)
    assert len(calls) == 2


def test_nobody_asking_means_no_fetches():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder, max_age_s=0.01)
    cache.get()
    assert _wait_for(lambda: len(calls) == 1)
    time.sleep(0.2)
    assert len(calls) == 1  # never polls UEFN on its own


def test_callers_get_copies():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder)
    cache.get()
    assert _wait_for(lambda: cache.value is not None)
    got = cache.get()
    got["mutated"] = True
    assert "mutated" not in cache.get()


def test_refresh_on_demand_fetches_even_when_fresh():
    calls = []
    cache = ListenerStatusCache(_slow_fetch(0.0, calls), _placeholder, max_age_s=60)
    cache.get()
    assert _wait_for(lambda: len(calls) == 1)
    cache.refresh()
    assert _wait_for(lambda: len(calls) == 2)


def test_a_failing_fetch_does_not_wedge_the_cache():
    n = []

    def fetch():
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("UEFN busy")
        return {"online": True, "status_text": "Online"}

    cache = ListenerStatusCache(fetch, _placeholder, max_age_s=0.0)
    cache.get()
    assert _wait_for(lambda: cache.value is not None)
    assert cache.get()["status_text"] == "Offline"
    assert _wait_for(lambda: cache.get()["status_text"] == "Online")


def test_panel_api_get_listener_status_does_not_block_on_a_slow_fetch(monkeypatch):
    from frontend.ui_web import panel_api

    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._listener_status_lock = threading.Lock()
    calls = []
    monkeypatch.setattr(api, "_refresh_listener_status", _slow_fetch(3.0, calls), raising=False)
    t0 = time.perf_counter()
    status = api.get_listener_status()
    assert time.perf_counter() - t0 < 0.05
    assert status["status_text"] == "Checking…"
