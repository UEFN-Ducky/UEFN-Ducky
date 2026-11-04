"""duckyos_store_catalog sends the ~230 KB catalog only when the caller's copy is out of date."""

from __future__ import annotations

import pytest

from frontend.ui_web import panel_api  # noqa: F401 - loads panel_api_store the way the app does
from frontend.ui_web import panel_api_store as store


@pytest.fixture
def api(monkeypatch):
    import frontend.duckyos_account as account

    catalog = {"ok": True, "items": [{"slug": "a", "icon_data_url": "x" * 1000}]}
    monkeypatch.setattr(account, "account_key", lambda: "acct")
    monkeypatch.setattr(account, "store_catalog", lambda: {k: (list(v) if isinstance(v, list) else v) for k, v in catalog.items()})
    monkeypatch.setattr(store, "_cache_store_catalog", lambda result, acct="": {**result, "account": acct})
    return store.PanelApiStoreMixin.__new__(store.PanelApiStoreMixin), catalog


def test_the_same_etag_gets_unchanged_and_no_items(api):
    panel, catalog = api
    first = panel.duckyos_store_catalog()
    assert first["items"] and first["etag"]
    again = panel.duckyos_store_catalog(first["etag"])
    assert again == {"ok": True, "unchanged": True, "etag": first["etag"], "account": "acct"}
    catalog["items"] = [{"slug": "b"}]  # the Store changed: the whole catalog comes back
    changed = panel.duckyos_store_catalog(first["etag"])
    assert changed["items"] == [{"slug": "b"}] and changed["etag"] != first["etag"]


def test_a_failed_read_never_says_unchanged(api, monkeypatch):
    import frontend.duckyos_account as account

    panel, _ = api
    tag = panel.duckyos_store_catalog()["etag"]

    def down():
        raise RuntimeError("offline")

    monkeypatch.setattr(account, "store_catalog", down)
    out = panel.duckyos_store_catalog(tag)
    assert out["ok"] is False and "unchanged" not in out and "etag" not in out
