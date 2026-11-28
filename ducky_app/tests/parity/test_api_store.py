"""The Store tab's reads: the installed plugins and a Store item's version notes. Both
backends ask a fake Store on this PC and must send it the same request and give the same
answer. Nothing here reaches the real site."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from backend.store.repos import kv, settings

from .conftest import ADK
from .parity_harness import plain

_NOTES = "patch_notes:"

@pytest.fixture
def store(fake, rust_store, python, login):
    """``store.both(method, *args)``: both backends' answer from the same start (what the
    first one kept of the notes is put back), with what each sent the Store."""

    class Store:
        site = fake

        @staticmethod
        def sign_in(**blob: Any) -> None:
            from backend.uefn_plugins import data_crypto

            account = login({"base_url": fake.url + "/", "email": "ducky@example.com", **blob})
            if "device_key" in blob:  # the data key's copy on this PC is wrapped with it
                assert data_crypto.adk(account) == ADK

        @staticmethod
        def both(method: str, *args: Any) -> tuple[Any, list[dict[str, Any]]]:
            before = kv.list_docs("cache_docs", _NOTES)
            answers = []
            for backend in (python, rust_store):
                for key in kv.list_docs("cache_docs", _NOTES):
                    kv.delete_doc("cache_docs", key)
                for key, doc in before.items():
                    kv.set_doc("cache_docs", key, doc)
                fake.calls.clear()
                answer = plain(backend.call(method, *args))
                answers.append((answer, list(fake.calls), kv.list_docs("cache_docs", _NOTES)))
            (theirs, their_calls, their_docs), (ours, our_calls, our_docs) = answers
            assert ours == theirs, f"{method}{args}:\n rust   {ours}\n python {theirs}"
            assert our_calls == their_calls, f"{method}{args} sent:\n rust   {our_calls}\n python {their_calls}"
            assert our_docs == their_docs, f"{method}{args} kept:\n rust   {our_docs}\n python {their_docs}"
            status, reply = theirs
            assert status == 200 and reply["ok"] is True, reply
            return reply["result"], their_calls

    return Store


_ROWS = [
    {"version": " 1.2.0 ", "changelog": " Faster lists. \n", "created_at": " 2026-11-01T10:00:00Z "},
    {"version": "", "changelog": "no version: skipped"},
    "not a row",
    {"version": 7, "changelog": None, "created_at": ""},
    {"version": "1.0.0", "extra": "dropped"},
]


def test_version_notes_are_fetched_once_and_then_answered_from_the_copy(store):
    store.sign_in(device_key="device-token-1")
    store.site.says({"ok": True, "payload": {"versions": _ROWS}})
    notes, calls = store.both("duckyos_store_versions", " demo-plugin ", "")
    assert [row["version"] for row in notes["versions"]] == ["1.2.0", "7", "1.0.0"] and "cached" not in notes
    assert notes["versions"][0] == {"version": "1.2.0", "changelog": "Faster lists.", "created_at": "2026-11-01T10:00:00Z"}
    (call,) = calls
    assert call["path"] == "/api/v1/plugins/uefn-ducky-store/collect/item-versions" and call["body"] == {"slug": "demo-plugin"}
    assert call["headers"]["authorization"] == "Bearer device-token-1" and call["headers"]["origin"] == store.site.url
    assert call["headers"]["user-agent"].startswith("UEFN-Ducky/") and "cookie" not in call["headers"]

    # The copy answers while the Store's newest version is the copy's.
    store.site.says({"payload": {"versions": [{"version": "9.9.9"}]}})
    for latest in ("", "1.2.0", None):
        notes, calls = store.both("duckyos_store_versions", "demo-plugin", latest)
        assert notes["cached"] is True and len(notes["versions"]) == 3 and calls == []
    notes, calls = store.both("duckyos_store_versions", "demo-plugin", "9.9.9")
    assert [row["version"] for row in notes["versions"]] == ["9.9.9"] and len(calls) == 1
    # The copy is kept under the version that was asked for, listed or not.
    notes, _ = store.both("duckyos_store_versions", "demo-plugin", "10.0.0")
    assert kv.get_doc("cache_docs", _NOTES + "item:demo-plugin")["version"] == "10.0.0"


_ANSWERS = [
    ({"versions": [{"version": "2.0.0"}]}, 200, {}),  # no "payload": the answer itself
    ([1, 2], 200, {}),
    ({"payload": {"versions": "not a list"}}, 200, {}),
    ("", 204, {}),
    ({}, 404, {}),
    ({"error": "Nope"}, 403, {}),
    ({"error": "Plugin is not active", "payload": {"error": ""}}, 403, {}),
    ({"error": "outer", "payload": {"error": "inner words"}}, 400, {}),
    ({"error": "this Tenant is off"}, 402, {}),
    ("<html><body>Sign in to the Wi-Fi</body></html>", 200, {"Content_Type": "text/html"}),
    ("  <html>Attention Required! | Cloudflare</html>", 403, {"Content_Type": "text/html"}),
    ({"error": "challenge"}, 403, {"cf_mitigated": "challenge"}),
    ("<html>blocked</html>", 403, {"Server": "cloudflare"}),
    ({"error": "down"}, 503, {}),
    ("<h1>Bad gateway</h1>", 502, {}),
    ({}, 429, {}),
    ("{not json", 200, {}),
    ({"error": "wants a cookie"}, 401, {}),
    (b"\xff\xfe{}", 200, {}),
]


@pytest.mark.parametrize("answer", _ANSWERS, ids=[f"{status}-{n}" for n, (_, status, _) in enumerate(_ANSWERS)])
def test_an_answer_the_store_gives_reads_the_same_with_and_without_a_copy(store, answer):
    body, status, headers = answer
    store.sign_in(device_key="device-token-1")
    store.site.says(body, status, **headers)
    fresh, calls = store.both("duckyos_store_versions", "fresh-item", "1.0.0")
    assert len(calls) == 1
    kv.set_doc("cache_docs", _NOTES + "item:kept-item", {"version": "0.9.0", "versions": [{"version": "0.9.0", "changelog": "old", "created_at": None}]})
    kept, _ = store.both("duckyos_store_versions", "kept-item", "1.0.0")
    if "error" in fresh:
        assert fresh["versions"] == [] and kept["cached"] is True and (kept["error"], kept["code"]) == (fresh["error"], fresh["code"])


def test_a_website_session_sends_its_cookies_and_an_expired_one_says_so(store, python, rust_store):
    session = {"session_name": "duckyos_session_x", "session_value": "sess=1=", "csrf_name": "duckyos_csrf_x", "csrf_value": "csrf-9"}
    store.sign_in(**session)
    store.site.says({"payload": {"versions": [{"version": "3.0.0"}]}})
    _, (call,) = store.both("duckyos_store_versions", "by-cookie", "")
    assert call["headers"]["cookie"] == "duckyos_session_x=sess=1=; duckyos_csrf_x=csrf-9" and call["headers"]["x-csrf-token"] == "csrf-9"
    assert "authorization" not in call["headers"]

    # A dead session is signed out by the call, so each backend is asked right after
    # signing in.
    store.site.says({"error": "expired"}, 401)
    answers = []
    for backend in (python, rust_store):
        store.sign_in(**session)
        answers.append(plain(backend.call("duckyos_store_versions", "expired-item", "")))
    assert answers[0] == answers[1] and answers[0][1]["result"]["code"] == "session_expired", answers


def test_no_slug_and_a_store_that_cannot_be_reached_are_plain_refusals(store, python, rust_store):
    for slug in ("", "   ", None):
        refused, calls = store.both("duckyos_store_versions", slug, "1.0.0")
        assert refused["code"] == "bad_request" and calls == []
    # Signed out, the Store is asked as nobody at the site's address: here one that
    # cannot answer (https to a plain server). The words differ, the refusal does not.
    replies = [plain(backend.call("duckyos_store_versions", "anyone", ""))[1]["result"] for backend in (python, rust_store)]
    for reply in replies:
        assert (reply["ok"], reply["code"], reply["versions"]) == (False, "network", []) and reply["error"].startswith("Network error: "), reply
    assert kv.list_docs("cache_docs", _NOTES) == {}


def test_a_store_that_hangs_holds_up_no_other_call(store, rust_store):
    store.sign_in(device_key="device-token-1")
    store.site.says({"payload": {"versions": [{"version": "1.0.0"}]}})
    store.site.hold.clear()
    answers: list[Any] = []
    waiting = threading.Thread(target=lambda: answers.append(rust_store.call("duckyos_store_versions", "slow-item", "")), daemon=True)
    waiting.start()
    deadline = time.monotonic() + 10
    while not store.site.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert store.site.calls, "the Store was never asked"
    started = time.monotonic()
    for _ in range(20):
        status, reply = rust_store.call("list_uefn_plugins")
        assert status == 200 and reply["result"]["ok"] is True
    assert time.monotonic() - started < 5 and not answers
    store.site.hold.set()
    waiting.join(20)
    assert answers and answers[0][1]["result"]["versions"] == [{"version": "1.0.0", "changelog": "", "created_at": None}]


_PLUGINS = {
    "alpha": {"id": "alpha", "label": "Alpha", "description": "First", "version": "1.4.2", "source": "store", "secret_keys": ["API_KEY"]},
    "Beta": {"id": "Beta Pack", "version": 7, "default_enabled": False, "kind": "theme", "contributes": {"automations": {"nodes": [1, 2, 3], "templates": {"a": 1}, "note": "x", "count": 4}, "ui": {"panels": []}}},
    "zeta": {"label": "", "version": "2.0.0-beta+7", "source": "ai", "contributes": ["not an object"], "secret_keys": "not a list", "default_enabled": 0},
    "Gamma": {"id": "", "version": "v9", "description": 12},
    "delta": {"id": "9bad"},
    "has space": {"version": 3.9},
    "Bad.Name": {"label": "skipped: the folder is no plugin id"},
}


@pytest.mark.parametrize(
    "saved",
    [{}, {"enabled_uefn_plugins": ["alpha", "Beta Pack", "Not Valid!", 5], "trusted_local_uefn_plugins": ["ZETA", "nope"]}, {"enabled_uefn_plugins": []}],
    ids=["never-chosen", "chosen", "none-on"],
)
def test_installed_plugins_are_listed_the_same(store, saved):
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    root = appdata_uefn_plugins_dir()
    for folder, manifest in _PLUGINS.items():
        (root / folder).mkdir(parents=True)
        (root / folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "broken").mkdir()
    (root / "broken" / "plugin.json").write_text("{not json", encoding="utf-8")
    (root / "a-list").mkdir()
    (root / "a-list" / "plugin.json").write_text("[1, 2]", encoding="utf-8")
    (root / "no-manifest").mkdir()
    (root / "loose.txt").write_text("not a folder", encoding="utf-8")
    settings.replace_all(saved, {})

    listed, _ = store.both("list_uefn_plugins")
    ids = [row["id"] for row in listed["plugins"]]
    # Folders are listed by name: ignoring case on Windows, capitals first elsewhere.
    assert sorted(ids) == ["alpha", "beta_pack", "gamma", "has_space", "zeta"], ids
    by_id = {row["id"]: row for row in listed["plugins"]}
    assert by_id["beta_pack"]["contributes"]["automations"] == {"nodes": 3, "templates": 1, "note": "x", "count": 4}
    assert by_id["alpha"]["enabled"] is ("enabled_uefn_plugins" not in saved or "alpha" in saved["enabled_uefn_plugins"])


def test_no_plugins_folder_is_an_empty_list(store):
    assert store.both("list_uefn_plugins")[0] == {"ok": True, "plugins": []}
