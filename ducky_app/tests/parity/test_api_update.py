"""Is there a newer version: the update status a window shows, the background check and
the network test. Both backends ask the fake site's app-version feed and must give the
same answer, keep the same notes and count the same usage events. Nothing is downloaded."""

from __future__ import annotations

import time
from typing import Any

import pytest

from backend.store import db
from backend.store.repos import events, kv, settings
from frontend import __version__ as NOW

from .parity_harness import plain
from .test_api_app_info import _comparable, _row

_NOTES = "patch_notes:app"
NEXT = "999.0.0"
_OFF = {"phase": "off", "version": "", "done": 0, "total": 0, "error": "", "failures": 0}
_INSTALL = {"installed": True, "install_location": "D:\\Apps\\UEFN Ducky", "install_scope": "user"}
_ASSETS = [
    {"os": os_name, "arch": arch, "package": package, "url": f"/files/{os_name}-{arch}.{package}", "sha256": f"{os_name}{arch}{package}", "sig_url": "sig/one.minisig" if package == "appimage" else None}
    for os_name in ("windows", "linux", "macos")
    for arch in ("x64", "arm64")
    for package in ("setup", "appimage", "deb", "app-zip")
]


def _feed(version: str = NEXT, **more: Any) -> dict[str, Any]:
    """The feed's app-version object for ``version``, with an update pack and every asset."""
    feed = {
        "currentVersion": version,
        "releaseNotes": " Big. ",
        "versions": [_row(version, "Newest")],
        "installerUrl": "old/setup.exe",
        "installerSha256": "",
        "sha256": "ab12",
        "update": {"version": version, "packUrl": "/pack.zip", "manifestUrl": "/manifest.json", "manifestSha256": "cd34"},
        "assets": _ASSETS,
        **more,
    }
    return {name: value for name, value in feed.items() if value is not None}


@pytest.fixture
def update(fake, rust_store, python, monkeypatch):
    """``update.both(ask_python, ask_rust)``: each backend's answer from the same start (a
    backend that just started, on a PC that kept nothing), with what it sent the site, the
    notes it kept and the usage events it queued."""
    from frontend import app_stage, update_swap, usage_stats, version_check

    # Neither backend may get a version ready here: that is a download.
    monkeypatch.setattr(app_stage, "stage", lambda *args, **kwargs: pytest.fail("the check started a download"))
    monkeypatch.setattr(app_stage, "prune", lambda: None)

    def fresh() -> None:
        monkeypatch.setattr(version_check, "last_net_stage", "")
        monkeypatch.setattr(version_check, "last_fallback", "")
        monkeypatch.setattr(update_swap, "_state", dict(_OFF))
        monkeypatch.setattr(usage_stats, "_update_check_sent", False)
        monkeypatch.setattr(usage_stats, "_net_fail_sent", set())
        monkeypatch.setattr(usage_stats, "_update_checks_done", 0)
        monkeypatch.setattr(usage_stats, "_update_check_failed", False)
        monkeypatch.setattr(usage_stats, "_enabled_cache", None)
        settings.replace_all({}, {})
        usage_stats.clear_queue()
        kv.delete_doc("cache_docs", _NOTES)
        conn = db.connect()
        with db.write_txn(conn):
            conn.execute("DELETE FROM meta WHERE key LIKE 'usage.%' OR key = 'whats_new.last_seen'")

    class Update:
        site = fake
        python_call, rust_call, rust = python.call, rust_store.call, rust_store

        @staticmethod
        def both(ask_python: Any, ask_rust: Any, start: Any = None, counted: tuple[str, ...] = ()) -> tuple[Any, list, list]:
            seen = []
            for ask in (ask_python, ask_rust):
                fresh()
                if start:
                    start()
                fake.calls.clear()
                answer = _comparable(plain(ask()))
                usage_stats.queued()  # the rows Python queued reach the table
                queued = [(row["payload"]["name"], row["payload"]["props"]) for row in events.oldest(usage_stats.KIND, limit=100)]
                seen.append((answer, list(fake.calls), kv.get_doc("cache_docs", _NOTES), [row for row in queued if not counted or row[0] in counted]))
            theirs, ours = seen
            for what, mine, other in zip(("answered", "sent", "kept", "counted"), ours, theirs):
                assert mine == other, f"{what}:\n rust   {mine}\n python {other}"
            return theirs[0], theirs[1], theirs[3]

        @classmethod
        def method(cls, name: str, *args: Any, **more: Any) -> tuple[Any, list, list]:
            (status, reply), calls, queued = cls.both(lambda: cls.python_call(name, *args), lambda: cls.rust_call(name, *args), **more)
            assert status == 200 and reply["ok"] is True, reply
            return reply["result"], calls, queued

    return Update


@pytest.mark.parametrize("feed", [({"payload": _feed()}, 200), ({"error": "gone"}, 404)], ids=["update", "no-feed"])
def test_this_copys_update_status_is_the_same_and_counted_once(update, feed):
    update.site.says(*feed)
    status, calls, queued = update.method("get_app_update_status")
    # Not an installed copy: nothing to update with, whatever the feed says.
    assert status["local_version"] == NOW and status["update_available"] is False and status["channel"] == "portable" and status["feed_status"] == "none"
    assert status["download_url"].endswith("/download") and ("update.check", {"status": "none", "channel": "portable", "pack": "no"}) in queued
    if calls and feed[1] == 404:
        (failed,) = [props for name, props in queued if name == "error.code"]
        assert failed["code"] == "update_check" and failed["net_stage"] == "http" and failed["reason"] == "http_404" and failed["attempt"] == "first"
    elif calls:
        assert status["remote_version"] == NEXT and status["release_notes"] == "Big." and kv.get_doc("cache_docs", _NOTES)["version"] == NOW


_INSTALLED = {
    "an update with its pack": ({"payload": _feed()}, 200, False),
    "the background download is stuck": ({"payload": _feed()}, 200, True),
    "a feed from before assets": ({"payload": _feed(assets=None)}, 200, False),
    "a feed with no pack": ({"payload": _feed(update=None)}, 200, False),
    "a pack with a link missing": ({"payload": _feed(update={"version": NEXT, "packUrl": "/pack.zip", "manifestUrl": " ", "manifestSha256": "cd34"})}, 200, False),
    "no installer at all": ({"payload": _feed(assets=None, installerUrl=None)}, 200, False),
    "the running version": ({"payload": _feed(NOW)}, 200, False),
    "an older version": ({"payload": _feed("0.0.1")}, 200, False),
    "not a version": ({"payload": _feed("next")}, 200, False),
    "paused": ({"payload": _feed(downloadsPaused=" TRUE ")}, 200, False),
    "paused as a flag": ({"payload": _feed(downloadsPaused=True)}, 200, False),
    "not paused": ({"payload": _feed(downloadsPaused="no")}, 200, False),
    "no version named": ({"payload": {"unrelated": True}}, 200, False),
    "an empty feed": ({"payload": {}}, 200, False),
    "gone": ({"error": "gone"}, 404, False),
    "not json": ("<html>Sign in to the Wi-Fi</html>", 200, False),
}


@pytest.fixture
def installed(update, monkeypatch):
    """``installed(stuck, start=None)``: both backends' update status as an installed copy
    whose background download is stuck or not."""
    from frontend import install_info, version_check

    monkeypatch.setattr(install_info, "get_install_info", lambda: dict(_INSTALL))
    monkeypatch.setattr(version_check, "is_packaged_runtime", lambda: True)

    def ask(stuck: bool, start: Any = None) -> tuple[Any, list, list]:
        monkeypatch.setattr(version_check, "_background_update_stuck", lambda: stuck)
        named = {"install": _INSTALL, "channel": "installed", "stuck": stuck}
        return update.both(version_check._app_update_status, lambda: update.rust.request("/__rs/update/status", named)[1], start=start, counted=("error.code",))

    return ask


@pytest.mark.parametrize("case", _INSTALLED, ids=[name.replace(" ", "-") for name in _INSTALLED])
def test_an_installed_copys_update_status_is_the_same(update, installed, case):
    body, code, stuck = _INSTALLED[case]
    update.site.says(body, code)
    status, calls, queued = installed(stuck)
    assert len(calls) == 1 and status["channel"] == "installed" and status["installed"] is True and status["install_location"] == _INSTALL["install_location"]
    expected = {
        "an update with its pack": "update_available", "the background download is stuck": "update_available", "a feed from before assets": "update_available",
        "a feed with no pack": "update_available", "a pack with a link missing": "update_available", "no installer at all": "update_available",
        "the running version": "up_to_date", "an older version": "up_to_date", "not a version": "up_to_date", "paused": "paused", "paused as a flag": "paused",
        "not paused": "update_available", "no version named": "no_release", "an empty feed": "no_release", "gone": "error", "not json": "error",
    }
    assert status["feed_status"] == expected[case], status
    assert status["update_available"] is (expected[case] == "update_available") and (status["error"] is None) == (expected[case] != "error")
    if case == "a feed from before assets":
        assert status["installer_url"] == f"{update.site.url}/old/setup.exe" and status["installer_sha256"] == "ab12"
    if case == "no installer at all":
        assert status["installer_url"] is None and status["installer_sha256"] is None
    if case in ("the background download is stuck", "a feed with no pack", "a pack with a link missing"):
        assert status["update_pack"] is False
    if expected[case] == "error":
        assert status["net_stage"] == ("http" if code == 404 else "other") and len(queued) == (code == 404)


def test_an_installed_copy_with_no_feed_answer_shows_the_notes_it_kept(update, installed):
    update.site.says({"payload": {}})
    kept = {"version": "0.0.2", "versions": [_row("0.0.2", "Old")]}
    status, _, _ = installed(False, start=lambda: kv.set_doc("cache_docs", _NOTES, kept))
    assert status["versions"] == kept["versions"] and status["feed_status"] == "no_release"


_KINDS = ("source", "appimage", "deb", "rpm", "flatpak", "aur", "installer", "app", "snap")
_PAYLOADS = [_feed(), _feed(assets=None), _feed(NOW), _feed("next"), _feed(downloadsPaused="1", notes="Held"), _feed(releaseNotes="", notes="Other notes"), {"unrelated": True}, {}, None]


def test_a_linux_copys_update_status_is_the_same_for_every_install_kind(update):
    from frontend import app_versions, updater

    failed = app_versions.appimage_swap_paths()[2]
    failed.parent.mkdir(parents=True, exist_ok=True)
    update.site.says({"payload": _feed()})
    for report in ("", '{"from": "9.9.9", "to": "1.0.0", "at": 5}', "[1]", "{broken"):
        failed.write_text(report, encoding="utf-8")
        for kind in _KINDS:
            for payload in _PAYLOADS if report == "" else _PAYLOADS[:1]:
                named = {"kind": kind} if payload is None else {"kind": kind, "payload": payload}
                theirs = _comparable(plain(updater.linux_update_status(payload=payload, kind=kind)))
                ours = _comparable(update.rust.request("/__rs/update/linux", named)[1])
                assert ours == theirs, f"{kind} {payload} {report!r}:\n rust   {ours}\n python {theirs}"
                assert theirs["install_kind"] == kind and (theirs["last_failure"] is not None) == report.startswith('{"from"')
    assert theirs["update_action"] == "none" and theirs["feed_status"] == "update_available" and theirs["asset"] is None


def test_the_update_row_starts_off_and_a_windows_report_changes_nothing(update):
    assert update.method("get_auto_update_status")[0] == _OFF
    # Only Ducky's own window says whether it is in use: never a call over HTTP.
    report = {"window": "main", "visible": True, "idle_s": 3, "dirty": 0, "jobs": []}
    assert update.python_call("report_update_ui", report) == (200, {"ok": True, "result": None})
    assert update.rust_call("report_update_ui", report) == (403, {"ok": False, "error": "method not allowed"})
    assert update.method("get_auto_update_status")[0] == _OFF
    assert update.method("get_update_progress")[0] == {"stage": "idle", "downloaded_bytes": 0, "total_bytes": 0, "error": None}


def test_check_for_updates_answers_at_once_and_checks_behind(update):
    update.site.says({"payload": _feed()})
    assert update.method("check_auto_update_now")[0] == _OFF
    # The Rust backend's own check then asks the feed (a rehearsal feed on this PC), and
    # leaves the found version in the row without getting it ready.
    deadline = time.monotonic() + 20
    row = {}
    while row.get("phase") != "idle" and time.monotonic() < deadline:
        time.sleep(0.05)
        row = update.rust_call("get_auto_update_status")[1]["result"]
    assert row == {**_OFF, "phase": "idle", "version": NEXT} and update.site.calls


_CHECKS = {
    "nothing newer": ({"payload": _feed(NOW)}, 200),
    "paused": ({"payload": _feed(downloadsPaused=True)}, 200),
    "no pack": ({"payload": _feed(update=None)}, 200),
    "an empty feed": ({"payload": {}}, 200),
    "gone": ({"error": "gone"}, 404),
    "not json": ("{not json", 200),
}


def _checks(update: Any) -> tuple[Any, Any]:
    """One check by each backend, answering the update found and the row after it."""
    from frontend import update_swap

    return (lambda: {"update": update_swap.check_and_stage(), "status": update_swap.status()}), (lambda: update.rust.request("/__rs/update/check", {})[1])


@pytest.mark.parametrize("case", _CHECKS, ids=[name.replace(" ", "-") for name in _CHECKS])
def test_a_check_that_finds_nothing_leaves_the_same_row(update, case):
    update.site.says(*_CHECKS[case])
    answer, calls, queued = update.both(*_checks(update))
    assert answer["update"] is None and answer["status"]["phase"] == "idle" and answer["status"]["version"] == "" and len(calls) == 1
    assert bool(answer["status"]["error"]) == (case in ("gone", "not json")) and [name for name, _ in queued] == (["error.code"] if case == "gone" else [])


def test_a_check_that_works_after_a_failed_one_is_counted(update):
    def twice(check: Any) -> Any:
        def run() -> list:
            update.site.says({"error": "gone"}, 404)
            failed = check()
            update.site.says({"payload": _feed(NOW)})
            return [failed, check(), check()]

        return run

    python_check, rust_check = _checks(update)
    answers, _, queued = update.both(twice(python_check), twice(rust_check))
    assert answers[0]["status"]["error"].endswith("(Not Found)") and answers[1]["status"]["error"] == ""
    assert [name for name, _ in queued] == ["error.code", "update.check_recovered"] and queued[1][1] == {"v": NOW}


def test_the_rust_check_names_a_newer_pack_and_gets_nothing_ready(update):
    feed = _feed()
    update.site.says({"payload": feed})
    status, answer = update.rust.request("/__rs/update/check", {})
    assert status == 200 and answer == {"update": feed["update"], "status": {**_OFF, "phase": "idle", "version": NEXT}}
    assert [call["path"] for call in update.site.calls] == ["/api/v1/plugins/uefn-ducky-store/collect/app-version"]


def test_the_network_test_reads_this_pcs_route_the_same(update):
    def gist(answer: Any) -> Any:
        status, reply = answer
        test = reply["result"]
        leg = test["download"]
        return [status, sorted(test), test["host"], test["proxy_mode"], test["proxy"], {name: value for name, value in test["dns"].items() if name != "ms"},
                test["ipv4"]["ok"], test["ipv6"]["ok"], test["https"]["ok"], test["https"].get("status"), test["ok"], sorted(leg), leg["host"], leg["ok"], leg["ipv4"]["ok"], leg["ipv6"]["ok"]]

    # The fake site speaks no HTTPS: it answers the test's request, and the secure start of
    # the download leg fails on it.
    theirs, ours = gist(update.python_call("run_network_test")), gist(update.rust_call("run_network_test"))
    assert ours == theirs, f"\n rust   {ours}\n python {theirs}"
    assert theirs[2] == "127.0.0.1" and theirs[4] == "direct" and theirs[8] is True and theirs[10] is True and theirs[13] is False


def test_a_used_pc_is_remembered_as_one(update):
    saved = {"walkthrough_completed": True}
    update.site.says({"payload": {"currentVersion": NOW, "versions": [_row(NOW, "Newest")]}})
    update.method("get_whats_new", start=lambda: settings.replace_all(dict(saved), {}))
    settings.forget_snapshot()  # the Rust backend wrote last
    assert settings.load_fields()["starter_llm_gateways_seeded"] is True
