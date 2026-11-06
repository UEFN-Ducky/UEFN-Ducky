"""INVENTORY must list every js_api method; WINDOW_INVENTORY every pywebview window call."""

from __future__ import annotations

from frontend.native_host import inventory


def test_every_js_api_method_is_listed():
    scanned = inventory.scan_js_api()
    missing = sorted(set(scanned) - set(inventory.INVENTORY))
    assert not missing, f"add these js_api methods to native_host/inventory.py _JS_API: {missing}"


def test_no_stale_js_api_entries():
    scanned = inventory.scan_js_api()
    stale = sorted(set(inventory.INVENTORY) - set(scanned))
    assert not stale, f"these INVENTORY names are gone from PanelApi: {stale}"


def test_entries_name_their_defining_class():
    scanned = inventory.scan_js_api()
    wrong = {n: (e["cls"], scanned[n]) for n, e in inventory.INVENTORY.items() if scanned.get(n) != e["cls"]}
    assert not wrong, wrong


def test_every_lean_cut_entry_has_a_handler():
    for name, entry in inventory.INVENTORY.items():
        assert entry["kind"] == "js_api"
        if entry["lean_cut"]:
            assert entry["handler"], name
        else:
            assert entry["handler"] is None, name


def test_overrides_name_real_methods():
    names = set(inventory.INVENTORY)
    assert set(inventory.LAUNCHER_HANDLERS) <= names
    assert set(inventory.NOT_LEAN_CUT) <= names
    assert not set(inventory.LAUNCHER_HANDLERS) & inventory.NOT_LEAN_CUT


def test_window_bounds_example():
    assert inventory.INVENTORY["get_window_bounds"] == {
        "kind": "js_api",
        "lean_cut": True,
        "handler": "window.bounds",
        "cls": "PanelApiWindowMixin",
    }
    assert inventory.handled_by_launcher("minimize_window")
    assert not inventory.handled_by_launcher("send_message")
    assert not inventory.handled_by_launcher("browser_pane_open")
