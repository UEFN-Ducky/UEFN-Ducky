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


def test_every_window_call_is_listed():
    scanned = inventory.scan_window_calls()
    missing = sorted(set(scanned) - set(inventory.WINDOW_INVENTORY))
    assert not missing, f"add these pywebview/tray calls to WINDOW_INVENTORY: {missing}"
    for name, hit in scanned.items():
        assert inventory.WINDOW_INVENTORY[name]["kind"] == hit["kind"], name


def test_no_stale_window_entries():
    stale = sorted(set(inventory.WINDOW_INVENTORY) - set(inventory.scan_window_calls()))
    assert not stale, f"these WINDOW_INVENTORY calls are gone from the window code: {stale}"


def test_window_scan_sees_calls_dialogs_and_events(tmp_path, monkeypatch):
    src = tmp_path / "frontend" / "x.py"
    src.parent.mkdir()
    src.write_text(
        "import webview\n"
        "def f(window, icon):\n"
        "    webview.create_window('a')\n"
        "    window.create_file_dialog(1)\n"
        "    window.events.shown += f\n"
        "    window.move(1, 2)\n"
        "    icon.stop()\n"
        "    other.move(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(inventory, "DUCKY_APP", tmp_path)
    found = inventory.scan_window_calls(("frontend/x.py",))
    assert {k: v["kind"] for k, v in found.items()} == {
        "webview.create_window": "webview",
        "window.create_file_dialog": "dialog",
        "window.events.shown": "event",
        "window.move": "window",
        "tray.stop": "tray",
    }


def test_window_lean_cut_entries_have_handlers():
    for name, entry in inventory.WINDOW_INVENTORY.items():
        assert entry["handler"] if entry["lean_cut"] else True, name


def test_export_json_is_stable_and_sorted():
    import json

    a, b = inventory.export_json(), inventory.export_json()
    assert a == b
    data = json.loads(a)
    assert list(data) == ["js_api", "window"]
    assert list(data["js_api"]) == sorted(data["js_api"])
    assert list(data["window"]) == sorted(data["window"])
    assert len(data["js_api"]) == len(inventory.INVENTORY)


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
    assert inventory.INVENTORY["browser_pane_open"]["handler"] == "python"
