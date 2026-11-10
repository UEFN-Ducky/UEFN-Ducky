import time
from backend.bridge import client


def test_response_cache_limits_and_idle_expiry(monkeypatch):
    monkeypatch.setattr(client, '_response_cache', {})
    monkeypatch.setattr(client, '_CACHE_MAX_ENTRIES', 4)
    monkeypatch.setattr(client, '_CACHE_MAX_BYTES', 8000)
    for n in range(30):
        client._remember_response(('list_assets', str(n)), {'data': 'x' * 200}, .05)
    assert len(client._response_cache) <= 4
    assert sum(v[2] for v in client._response_cache.values()) <= 8000
    client._remember_response(('list_assets', 'huge'), {'data': 'x' * 9000}, 30)
    assert ('list_assets', 'huge') not in client._response_cache
    deadline = time.monotonic() + 2
    while client._response_cache and time.monotonic() < deadline:
        time.sleep(.01)
    assert not client._response_cache
