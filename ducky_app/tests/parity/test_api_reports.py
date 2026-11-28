"""Crash reports and the page's own error reports: both backends scrub the same text to
the same result, list, send and delete the same report files, and keep the same rows for
what the page reports. Reports go to a fake site on this PC; nothing reaches the real one."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from .parity_harness import plain

ROUTE = "/api/v1/plugins/custom-forms/collect/submit"
_LOGIN = {
    "email": "ducky@example.com", "device_key": "device-token-1", "device_key_id": "key-mine",
    "session_name": "duckyos_session_x", "session_value": "sess-1", "csrf_name": "duckyos_csrf_x", "csrf_value": "csrf-1",
}  # fmt: skip
_SECRETS = ("device-token-1", "sess-1", "csrf-1", "ducky@example.com")
QUEUE = "usage_queue"

# Text with each thing a report must never carry, and text that only looks like it.
_TEXTS = (
    "",
    "nothing personal here",
    "{home}\\Documents\\island.verse and {home_slashes}/x and {home_doubled}\\\\y",
    "{HOME_UPPER}\\Desktop",
    r"Plugin failed for someone@example.com at C:\Users\otherperson\Desktop\a.glb",
    r"c:/users/Other.Person/x D:\\Users\\bob\\y C:\Users\Public\z C:\Users\Default\z C:\Users\Defaults\z C:\Users\public2",
    'File "/home/alice/app.py", line 3 and /Users/pat/Library/x, /home/default/x, see https://example.com/home/page',
    "Authorization: Bearer abcdefghijklmnop and Basic dXNlcjpwYXNz and token ghp_BBBBBBBBBBBBBBBBBBBBBBBB",
    'api_key=supersecretvalue password: hunter22 {"client_secret": "abc123def"} PWD=x1 Cookie: sid=abcdef; token = "tok_12345"',
    "url=https://bob:pw123@host.example/x?X-Amz-Signature=deadbeef&size=3&code=abc#frag ftp://u:p@h/",
    "sk-proj-AAAAAAAAAAAAAAAAAAAA sk-ant-short sk_live_0123456789abcdef0123 github_pat_0123456789abcdefghij_x xoxb-123456789012-abc",
    "AKIAABCDEFGHIJKLMNOP AKIAABCDEFGHIJKLMNOPQ AIzaSyA0123456789abcdefghijklmnopqrstuv xai-abcdefghij0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "from 203.0.113.7 via 127.0.0.1:4199, 0.0.0.0, 10.0.26300, 999.1.1.1, 1.2.3.4.5, 127.0.0.1.5 and 192.168.1.255",
    "UEFN Ducky 1.2.357 on Windows 10.0.26300",
    "[2026-10-09 10:00:00] (agent) max_tokens: 4096, tokens used 1200",
    "Ünïcödé someone@exämple.com at C:\\Users\\Zoë\\x — /home/josé/y",
    "line one\nC:\\Users\\bob\nsecret=abc\tnext\r\npassword=\n",
    "a@b.co " * 400 + "10.9.8.7 " * 400,
)
_ERRORS = (
    "", "all fine", "Timed out waiting for listener idle", "HTTP 503 overloaded", "[WinError 10013] forbidden", "session is closed",
    "Connection refused", "429 Too Many Requests", "401 Unauthorized", "prompt is too long", "image exceeds the size", "400 bad request",
    "502 Bad Gateway", "MemoryError", "Permission denied", "No such file", "tools didn't connect", "process exited", "Traceback (most recent call last)",
    "TIMEOUT", "İstanbul offline",
)  # fmt: skip
_SOURCES = ("", "ui", "  UI: Chat  load ", "MCP-bridge", "verse_lsp", "py.ValueError", "__", "a__b", "x" * 80, "Übung macht", "plugin/store.install")


def _home_spellings() -> dict[str, str]:
    home = str(Path.home())
    return {"home": home, "home_slashes": home.replace("\\", "/"), "home_doubled": home.replace("\\", "\\\\"), "HOME_UPPER": home.upper()}


def test_text_is_scrubbed_and_named_the_same(rust_store) -> None:
    from backend.util.privacy import scrub
    from frontend import usage_events

    def ours(what: str, text: str) -> Any:
        status, answer = rust_store.request(f"/__rs/reports/{what}", {"text": text})
        assert status == 200, answer
        return answer

    home = _home_spellings()
    for raw in _TEXTS:
        text = raw.format(**home) if "{home" in raw or "{HOME" in raw else raw
        assert ours("scrub", text) == scrub(text), text
    out = scrub(" ".join((_TEXTS[2].format(**home), _TEXTS[4], _TEXTS[6], _TEXTS[8])))
    for gone in (home["home"], "someone@example.com", "otherperson", "alice", "pat", "supersecretvalue", "hunter22", "abc123def"):
        assert gone not in out, gone
    for text in _ERRORS:
        assert ours("kind", text) == usage_events.kind_of(text), text
    for text in _SOURCES:
        assert ours("where", text) == usage_events.where_of(text), text
        assert ours("code", text) == usage_events.code_of(text), text


_REPORT = {"message": "UEFN Ducky closed by itself", "app_version": "1.2.3", "error_log": "Why: it ran out of memory\nNo crash trace"}
_FILES = {
    "kept-20261101-101010.json": json.dumps(_REPORT),
    "sent-20261102-090000.json": json.dumps({"message": "Sent before", "error_log": "x"}),
    "kept-20261103-000000-2.json": json.dumps({"message": "Zoë — ünïcödé", "app_version": 7, "error_log": ""}),
    "kept-20261103-000000.json": json.dumps({"error_log": "only a log"}),
    "kept-20261104-000000.json": "[1, 2]",
    "kept-20261105-000000.json": "not json",
    "sent-20261101-101010.json": "{}",
    # Not reports.
    "kept-2026110-101010.json": "{}", "held-20261101-101010.json": "{}", "kept-20261101-101010.txt": "{}", "boot.json": "{}",
    "kept-20261101-101010-.json": "{}", "xkept-20261101-101010.json": "{}",
}  # fmt: skip


@pytest.fixture
def crash(fake, rust_store, python, login, monkeypatch, tmp_path):
    """``crash.folder`` holds the report files; ``crash.fill()`` puts ``_FILES`` back;
    ``crash.names()`` lists it; ``crash.calls`` are the two backends."""
    from frontend import boot_report

    monkeypatch.setattr(boot_report, "COLLECT_URL", fake.url + ROUTE)  # Rust sends to the signed-in fake site
    login({"base_url": fake.url + "/", **_LOGIN})

    class Crash:
        folder = boot_report._dir()
        calls = (python.call, rust_store.call)
        site = fake

        @classmethod
        def fill(cls) -> None:
            cls.folder.mkdir(parents=True, exist_ok=True)
            for old in cls.folder.iterdir():
                old.unlink()
            for n, (name, text) in enumerate(_FILES.items()):
                (cls.folder / name).write_text(text, encoding="utf-8")
                os.utime(cls.folder / name, (1_795_000_000.123456 + n, 1_795_000_000.123456 + n))

        @classmethod
        def names(cls) -> list[str]:
            return sorted(path.name for path in cls.folder.iterdir())

    assert tmp_path in Crash.folder.parents
    return Crash


def test_reports_are_listed_the_same(crash) -> None:
    crash.fill()
    theirs, ours = (plain(call("crash_reports")) for call in crash.calls)
    assert ours == theirs
    reports = theirs[1]["result"]["reports"]
    assert [row["id"] for row in reports][:3] == ["kept-20261105-000000", "kept-20261104-000000", "kept-20261103-000000-2"]
    assert len(reports) == 7 and all(row["at"] > 1_795_000_000 for row in reports)
    by_id = {row["id"]: row for row in reports}
    assert by_id["kept-20261101-101010"] == {
        "id": "kept-20261101-101010", "sent": False, "at": by_id["kept-20261101-101010"]["at"], "message": "UEFN Ducky closed by itself",
        "text": "UEFN Ducky closed by itself\n\nApp 1.2.3\n\nWhy: it ran out of memory\nNo crash trace",
    }  # fmt: skip
    assert by_id["sent-20261102-090000"]["sent"] is True and by_id["sent-20261102-090000"]["text"] == "Sent before\n\nApp ?\n\nx"
    assert by_id["kept-20261103-000000-2"]["text"] == "Zoë — ünïcödé\n\nApp 7"
    assert by_id["kept-20261105-000000"]["text"] == by_id["kept-20261104-000000"]["text"] == "App ?"
    # No folder yet: no reports.
    for old in crash.folder.iterdir():
        old.unlink()
    crash.folder.rmdir()
    assert [plain(call("crash_reports")) for call in crash.calls] == [[200, {"ok": True, "result": {"reports": []}}]] * 2


def test_a_kept_report_is_sent_once_and_any_report_is_deleted(crash) -> None:
    steps = [
        ("crash_report_send", "kept-20261101-101010"), ("crash_report_send", "kept-20261101-101010"), ("crash_report_send", "sent-20261102-090000"),
        ("crash_report_send", "kept-20261103-000000-2"), ("crash_report_send", "kept-20261105-000000"), ("crash_report_send", "kept-20991231-000000"),
        ("crash_report_send", "../boot"), ("crash_report_send", "boot"), ("crash_report_send", ""), ("crash_report_send", None), ("crash_report_send",),
        ("crash_report_send", 5), ("crash_report_send", "kept-20261104-000000\n"),
        ("crash_report_delete", "sent-20261101-101010"), ("crash_report_delete", "sent-20261101-101010"), ("crash_report_delete", "kept-20261104-000000"),
        ("crash_report_delete", "boot"), ("crash_report_delete", "..\\boot"), ("crash_report_delete", ""), ("crash_report_delete",),
        ("crash_report_delete", "KEPT-20261103-000000"),
    ]  # fmt: skip
    seen = []
    for call in crash.calls:
        crash.fill()
        crash.site.calls.clear()
        answers = [plain(call(*step)) for step in steps]
        sent = [{**row, "headers": {name: value for name, value in row["headers"].items() if name != "accept"}} for row in crash.site.calls]
        seen.append((answers, sent, crash.names()))
    theirs, ours = seen
    for name, mine, yours in zip(("answers", "what the site got", "files left"), ours, theirs):
        assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
    answers, sent, left = theirs
    refused = {"ok": False, "error": "Couldn't send the report. Check your connection and try again."}
    said = [answer[1]["result"] for answer in answers]
    assert said[:5] == [{"ok": True}, refused, refused, {"ok": True}, {"ok": True}] and all(answer == refused for answer in said[5:13])
    assert said[13:] == [{"ok": True}, {"ok": False}, {"ok": True}] + [{"ok": False}] * 5
    # The file's own JSON, to the form, as nobody: no key and no cookie of the account.
    assert [row["path"] for row in sent] == [ROUTE] * 3 and sent[0]["body"] == _REPORT and sent[2]["body"] == {}
    assert sent[1]["body"] == {"message": "Zoë — ünïcödé", "app_version": 7, "error_log": ""}
    assert set(sent[0]["headers"]) == {"origin", "user-agent", "content-type"} and sent[0]["headers"]["origin"] == "https://uefnducky.org"
    assert sent[0]["headers"]["user-agent"].startswith("UEFN-Ducky/") and not any(secret in json.dumps(sent) for secret in _SECRETS)
    assert "sent-20261101-101010.json" not in left and "kept-20261104-000000.json" not in left and "boot.json" in left
    assert {"sent-20261103-000000-2.json", "sent-20261105-000000.json", "kept-20261103-000000.json"} <= set(left)

    # A site that says no, or is away: the report stays kept.
    for status in (500, 404, 302):
        crash.site.says({}, status=status, path=ROUTE)
        for call in crash.calls:
            crash.fill()
            assert plain(call("crash_report_send", "kept-20261101-101010")) == [200, {"ok": True, "result": refused}], status
            assert "kept-20261101-101010.json" in crash.names()


@pytest.fixture
def page(rust_store, python, monkeypatch):
    """``page.each(steps)``: the steps run by each backend from an empty log; per backend the
    answers, the rows kept per kind (without their times) and the usage events queued."""
    from backend.store.repos import events, settings
    from frontend import usage_stats

    monkeypatch.setattr(usage_stats, "_enabled_cache", None)

    def bare(row: dict) -> dict:
        row = {name: value for name, value in row.items() if name != "ts"}
        if isinstance(row.get("payload"), dict):
            row["payload"] = {name: value for name, value in row["payload"].items() if name != "ts"}
        return row

    class Page:
        @staticmethod
        def each(steps: list, on: bool = True) -> tuple[list, dict, list]:
            seen = []
            for call in (python.call, rust_store.call):
                settings.replace_all({} if on else {"usage_stats": False}, {})
                usage_stats._enabled_cache = None
                usage_stats.clear_queue()
                for kind in ("error", "ui_crash"):
                    events.clear(kind)
                answers = [plain(call(*step)) for step in steps]
                usage_stats.queued()  # the rows Python queued reach the table
                rows = {kind: [bare(row) for row in events.newest(kind, limit=100)] for kind in ("error", "ui_crash")}
                queued = [(row["payload"]["name"], row["payload"]["props"]) for row in events.oldest(QUEUE, limit=100)]
                seen.append((answers, rows, queued))
            theirs, ours = seen
            for name, mine, yours in zip(("answers", "rows", "usage events"), ours, theirs):
                assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
            return theirs

    return Page


def test_the_pages_error_is_one_scrubbed_line(page) -> None:
    home = str(Path.home())
    steps = [
        ("report_ui_error", "UI: Chat!", f"  could not load {home}\\x for someone@example.com, token=abcdef  "),
        ("report_ui_error", "UI: Chat!", f"could not load {home}\\x for someone@example.com, token=abcdef"),  # the same line again
        ("report_ui_error", "", "Timed out waiting for the chat"), ("report_ui_error", None, "x"), ("report_ui_error", "!!!", "y"),
        ("report_ui_error", "S" * 40, "z" * 3000), ("report_ui_error", "plugin_store", "   "), ("report_ui_error", "ui", ""), ("report_ui_error", "ui", None),
        ("report_ui_error",), ("report_ui_error", 7, 404), ("report_ui_error", "ui", "x"),
    ]  # fmt: skip
    answers, rows, queued = page.each(steps)
    assert [answer[1]["result"]["ok"] for answer in answers] == [True, True, True, True, True, True, False, False, False, False, True, True]
    errors = rows["error"][::-1]
    assert errors[0] == {"source": "uichat", "message": "could not load ~\\x for [email], token=[redacted]"}
    assert [row["source"] for row in errors] == ["uichat", "ui", "ui", "ui", "s" * 32, "7", "ui"]
    assert errors[4]["message"] == "z" * 1000 and errors[5]["message"] == "404" and home not in json.dumps(rows)
    # Counted every time, the repeated line too; only words leave.
    assert len(queued) == 8 and queued[0][0] == "error.code" and queued[0][1]["code"] == "uichat" and queued[0][1]["where"] == "ui"
    assert queued[2][1]["kind"] == "timeout" and all("message" not in props for _name, props in queued)
    # Stats off: the line is still kept, nothing is counted.
    _answers, rows, queued = page.each(steps[:1], on=False)
    assert len(rows["error"]) == 1 and queued == []


def test_a_page_crash_is_kept_scrubbed(page) -> None:
    home = str(Path.home())
    crash = {
        "label": "Chat panel", "message": f"Cannot read {home}\\a.json (password=hunter22)", "stack": "at f (C:\\Users\\bob\\x.js:1)\n" * 400,
        "componentStack": "in Chat\n" * 2000, "appVersion": "9.9.9", "pluginId": "p" * 200, "surface": "S" * 200, "faultKind": "Render Error!" * 5,
        "faultAction": "reload", "extra": "dropped",
    }  # fmt: skip
    steps = [
        ("report_ui_crash", crash), ("report_ui_crash", {}), ("report_ui_crash",), ("report_ui_crash", None), ("report_ui_crash", "text"),
        ("report_ui_crash", {"label": "Only a label", "message": 5, "stack": None, "appVersion": 0}),
        ("report_ui_crash", {"surface": "plugin-panel", "label": "x", "message": "Zoë — ünïcödé"}),
    ]  # fmt: skip
    answers, rows, queued = page.each(steps)
    assert all(answer == [200, {"ok": True, "result": {"ok": True, "path": "ducky.db:events/ui_crash"}}] for answer in answers)
    first = rows["ui_crash"][-1]
    assert first["source"] == "S" * 128 and first["message"] == "Cannot read ~\\a.json (password=[redacted])"
    payload = first["payload"]
    assert list(payload) == ["version", "label", "message", "stack", "componentStack", "appVersion", "pluginId", "surface", "faultKind", "faultAction"]
    assert len(payload["stack"]) == 8000 and "bob" not in payload["stack"] and len(payload["componentStack"]) == 8000
    assert (payload["appVersion"], len(payload["pluginId"]), len(payload["faultKind"]), payload["faultAction"]) == ("9.9.9", 128, 32, "reload")
    assert [row["source"] for row in rows["ui_crash"][::-1]] == ["S" * 128, "", "", "", "", "Only a label", "plugin-panel"]
    assert rows["ui_crash"][0]["message"] == "Zoë — ünïcödé" and rows["ui_crash"][1]["payload"]["message"] == "5"
    assert [name for name, _props in queued] == ["ui.crash"] * 7 and queued[1][1] == {"code": "boundary"} and queued[6][1] == {"code": "plugin-panel"}
