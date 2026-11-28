"""What a window asks about the app itself: its version, its patch notes, the What's new
window and whether this copy is installed. Both backends ask the fake site's app-version
feed and must send the same request, give the same answer and keep the same copy."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import pytest

from backend.store import db
from backend.store.repos import kv, settings
from frontend import __version__ as NOW

from .parity_harness import plain

_NOTES = "patch_notes:"
_LAST_SEEN = "whats_new.last_seen"
_FEED = "/api/v1/plugins/uefn-ducky-store/collect/app-version"
OLD, OLDER, NEXT = "0.0.2", "0.0.1", "999.0.0"


def _forget_last_seen() -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM meta WHERE key=?", (_LAST_SEEN,))


def _comparable(value: Any) -> Any:
    """An answer with the parts of an error only one backend can word left out: the JSON
    parser's own message, and the network library's in brackets (the feed's own error
    answers are worded the same by both)."""
    if isinstance(value, dict):
        return {key: _comparable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_comparable(item) for item in value]
    if isinstance(value, str) and value.startswith("Invalid JSON: ") and value != "Invalid JSON: expected object":
        return "Invalid JSON"
    if isinstance(value, str) and value.startswith("Can't reach the update server. UEFN Ducky "):
        return value.split(". (", 1)[0] + "."
    return value


@pytest.fixture
def app(fake, rust_store, python):
    """``app.both(method, *args, start=...)``: both backends' answer, each from the same
    start (no kept notes, nothing seen, default settings, then ``start()``)."""

    class App:
        site = fake

        @staticmethod
        def both(method: str, *args: Any, start: Any = None) -> tuple[Any, list[dict[str, Any]]]:
            seen = []
            for backend in (python, rust_store):
                for key in kv.list_docs("cache_docs", _NOTES):
                    kv.delete_doc("cache_docs", key)
                _forget_last_seen()
                settings.replace_all({}, {})
                if start:
                    start()
                fake.calls.clear()
                answer = _comparable(plain(backend.call(method, *args)))
                seen.append((answer, list(fake.calls), kv.list_docs("cache_docs", _NOTES), kv.meta_get(_LAST_SEEN)))
            theirs, ours = seen
            for what, mine, other in zip(("answered", "sent", "kept", "saved as seen"), ours, theirs):
                assert mine == other, f"{method}{args} {what}:\n rust   {mine}\n python {other}"
            status, reply = theirs[0]
            assert status == 200 and reply["ok"] is True, reply
            return reply["result"], theirs[1]

    return App


def _row(version: str, *texts: str, **more: Any) -> dict[str, Any]:
    return {"version": version, "changelog": f"Notes of {version}", "created_at": f"2026-11-0{len(texts) + 1}", "items": [{"kind": "new", "text": text} for text in texts], **more}


def test_the_version_is_the_running_apps(app):
    assert app.both("get_version") == (NOW, [])


def test_patch_notes_are_fetched_once_per_version(app):
    rows = [_row(f" {NOW} ", " Faster lists. ", notes="unused"), {"version": "", "changelog": "skipped"}, "not a row", {"version": 7, "notes": " Old ", "items": [{"kind": "odd", "text": "x"}, {"text": " "}, 5]}]
    app.site.says({"ok": True, "payload": {"currentVersion": NOW, "versions": rows}})
    notes, calls = app.both("get_app_patch_notes")
    assert notes["cached"] is False and notes["error"] is None and [row["version"] for row in notes["versions"]] == [NOW, "7"]
    assert notes["versions"][0]["items"] == [{"kind": "new", "text": "Faster lists."}] and notes["versions"][1] == {"version": "7", "changelog": "Old", "created_at": None, "items": [{"kind": "improved", "text": "x"}]}
    (call,) = calls
    assert call["path"] == _FEED and call["body"] == {} and call["headers"]["origin"] == app.site.url
    assert call["headers"]["user-agent"] == f"UEFN-Ducky/{NOW}" and "authorization" not in call["headers"]

    # The copy answers while it is the running version's; an older version's is replaced.
    kept = {"version": NOW, "versions": [_row(NOW, "Kept")]}
    notes, calls = app.both("get_app_patch_notes", start=lambda: kv.set_doc("cache_docs", _NOTES + "app", kept))
    assert notes == {"versions": kept["versions"], "cached": True, "error": None} and calls == []
    notes, calls = app.both("get_app_patch_notes", start=lambda: kv.set_doc("cache_docs", _NOTES + "app", {**kept, "version": OLD}))
    assert notes["cached"] is False and len(calls) == 1 and kv.get_doc("cache_docs", _NOTES + "app")["version"] == NOW


_FEEDS = [
    ({"currentVersion": NOW, "releaseNotes": " Bare. "}, 200),
    ({"handled": True, "payload": {"payload": {"currentVersion": NOW, "notes": "Nested"}}}, 200),
    ({"payload": {"version": 12, "versions": []}}, 200),
    ({"payload": {"current_version": " 3.0.0 ", "versions": "not a list"}}, 200),
    ({"payload": {"versions": [_row(OLD, "One")]}, "version": "ignored"}, 200),
    ({"payload": {"unrelated": True}}, 200),
    ({"payload": {}}, 200),
    ({}, 200),
    ([1, 2], 200),
    ("{not json", 200),
    ("<html>Sign in to the Wi-Fi</html>", 200),
    ("", 204),
    ({"currentVersion": NOW}, 201),
    ({"error": "gone"}, 404),
    ({"error": "no"}, 403),
    ({"error": "proxy wants a login"}, 407),
]


@pytest.mark.parametrize("feed", _FEEDS, ids=[f"{status}-{n}" for n, (_, status) in enumerate(_FEEDS)])
def test_a_feed_answer_reads_the_same_with_and_without_a_copy(app, feed):
    body, status = feed
    app.site.says(body, status)
    fresh, calls = app.both("get_app_patch_notes")
    assert len(calls) == 1 and fresh["cached"] is False
    old = {"version": OLD, "versions": [_row(OLD, "Old")]}
    kept, _ = app.both("get_app_patch_notes", start=lambda: kv.set_doc("cache_docs", _NOTES + "app", old))
    if fresh["error"]:
        assert fresh["versions"] == [] and kept == {"versions": old["versions"], "cached": True, "error": fresh["error"]}
    if status >= 400:
        assert fresh["error"].startswith("Can't reach the update server. ") and fresh["error"].endswith(")"), fresh


def test_a_busy_feed_is_asked_again(app):
    app.site.says({"error": "busy"}, 503)
    notes, calls = app.both("get_app_patch_notes")
    assert len(calls) == 5 and notes["error"] == "Can't reach the update server. 127.0.0.1 answered with an error. Try again in a minute. (Service Unavailable)"


@pytest.fixture
def no_feed(fake, monkeypatch):
    """The feed's address is a port on this PC that nothing listens on."""
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        monkeypatch.setenv("DUCKY_UPDATE_BASE_URL", f"http://127.0.0.1:{free.getsockname()[1]}")


def test_a_feed_that_cannot_be_reached_says_so(no_feed, app):
    notes, calls = app.both("get_app_patch_notes")
    assert notes["versions"] == [] and calls == []
    assert notes["error"] == "Can't reach the update server. UEFN Ducky can't connect to 127.0.0.1. If it opens in your browser, a firewall or antivirus is blocking UEFN Ducky: allow it, or press Test network."
    old = {"version": OLD, "versions": [_row(OLD, "Old")]}
    kept, _ = app.both("get_whats_new", start=lambda: kv.set_doc("cache_docs", _NOTES + "app", old))
    assert kept["error"] == notes["error"] and kept["show"] is False and [row["version"] for row in kept["recent"]] == [OLD]


def _plugin(name: str) -> None:
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    folder = appdata_uefn_plugins_dir() / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps({"id": name}), encoding="utf-8")


_USED = {"walkthrough_completed": True}
_ROWS = [_row(OLDER, "First"), _row(NOW, "Newest", "And more"), _row(OLD, "Second"), _row(OLD, "Listed twice"), _row(NEXT, "Not out here yet"), {"version": "0.0.3", "changelog": "no short changes"}]
_WINDOWS = {
    "a fresh install sees nothing": (None, {}, [], _ROWS),
    "a seeded PC is not fresh": (None, {"starter_llm_gateways_seeded": True}, [], _ROWS),
    "a finished walkthrough is a used PC": (None, _USED, [], _ROWS),
    "a plugin beyond the starter gateways is a used PC": (None, {}, ["blender"], _ROWS),
    "an enabled plugin is a used PC": (None, {"enabled_uefn_plugins": [" Blender ", ""]}, [], _ROWS),
    "the starter gateways alone are a fresh install": (None, {"enabled_uefn_plugins": ["OpenAI"]}, ["anthropic"], _ROWS),
    "an update shows what was not seen": (OLDER, _USED, [], _ROWS),
    "seen already": (NOW, _USED, [], _ROWS),
    "seen a newer one": (NEXT, _USED, [], _ROWS),
    "a blank last seen": ("", _USED, [], _ROWS),
    "not a version": ("next", _USED, [], _ROWS),
    "the feed does not list this build": (OLDER, _USED, [], _ROWS[:1]),
    "no short changes for this build": (OLDER, _USED, [], [{"version": NOW, "changelog": "Only long notes", "created_at": "2026-11-05"}, _row(OLDER, "First")]),
    "more versions than the window lists": (None, _USED, [], [_row(f"0.1.{n}", f"Change {n}") for n in range(8)] + [_row(NOW, "Newest")]),
}


@pytest.mark.parametrize("case", _WINDOWS, ids=[name.replace(" ", "-") for name in _WINDOWS])
def test_whats_new_shows_the_versions_this_pc_has_not_seen(app, case):
    last_seen, saved, plugins, rows = _WINDOWS[case]
    for name in plugins:
        _plugin(name)
    app.site.says({"payload": {"currentVersion": NOW, "versions": rows}})

    def start() -> None:
        settings.replace_all(dict(saved), {})
        if last_seen is not None:
            kv.meta_set(_LAST_SEEN, last_seen)

    window, _ = app.both("get_whats_new", start=start)
    assert window["current"] == NOW and window["last_seen"] == last_seen and window["error"] is None
    shown = [row["version"] for row in window["versions"]]
    if case == "an update shows what was not seen":
        assert window["show"] is True and shown == [NOW, OLD] and [row["version"] for row in window["recent"]] == [NOW, OLD, OLDER]
        assert window["versions"][0]["items"] == [{"kind": "new", "text": "Newest"}, {"kind": "new", "text": "And more"}] and window["released_at"] == "2026-11-03"
    if "fresh install" in case:
        assert window["show"] is False and window["mark"] == NOW and kv.meta_get(_LAST_SEEN) == NOW
    if "used PC" in case or case == "a seeded PC is not fresh":
        assert window["show"] is True and shown == [NOW] and window["mark"] is None
    if case == "more versions than the window lists":
        assert len(window["recent"]) == 5 and window["recent"][0]["version"] == NOW


def test_whats_new_with_no_feed_asks_again_next_start(app):
    app.site.says({"error": "gone"}, 404)
    window, _ = app.both("get_whats_new", start=lambda: settings.replace_all(dict(_USED), {}))
    assert window["show"] is False and window["mark"] is None and window["recent"] == [] and window["error"].endswith("(Not Found)")


@pytest.mark.parametrize("last_seen", [None, "", OLD, NEXT, "next"])
@pytest.mark.parametrize("version", ["", "   ", None, OLDER, f" {NOW} ", "not a version"])
def test_marking_a_version_seen_never_moves_back(app, last_seen, version):
    answer, calls = app.both("mark_whats_new_seen", version, start=lambda: last_seen is None or kv.meta_set(_LAST_SEEN, last_seen))
    assert answer == {"ok": True} and calls == []
    if last_seen == NEXT:
        assert kv.meta_get(_LAST_SEEN) == NEXT


def test_install_info_reads_the_same(app):
    # Installed or not is this PC's own state: both backends must read it the same.
    info, calls = app.both("get_install_info")
    assert isinstance(info["installed"], bool) and (info["install_location"] is not None) == info["installed"] and calls == []


def test_a_feed_that_hangs_holds_up_no_other_call(app, rust_store):
    app.site.says({"payload": {"currentVersion": NOW, "versions": [_row(NOW, "Newest")]}})
    app.site.hold.clear()
    answers: list[Any] = []
    waiting = threading.Thread(target=lambda: answers.append(rust_store.call("get_whats_new")), daemon=True)
    waiting.start()
    deadline = time.monotonic() + 10
    while not app.site.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.site.calls, "the feed was never asked"
    started = time.monotonic()
    for _ in range(20):
        assert rust_store.call("get_version")[1] == {"ok": True, "result": NOW}
        assert rust_store.call("get_install_info")[1]["result"]["installed"] is False
    assert time.monotonic() - started < 5 and not answers
    app.site.hold.set()
    waiting.join(20)
    assert answers and answers[0][1]["result"]["recent"][0]["version"] == NOW
