"""--remote-uefn-probe: a Mac runner signs in from DUCKY_TEST_ACCOUNT_TOKEN (memory only) and
has the account's PC run ping, a Verse build and a screenshot through a (fake) Ducky link."""

from __future__ import annotations

import base64
import io
import json
import sys

import urllib.request  # noqa: F401 — imported before the darwin patch (it would load _scproxy)

import pytest

from frontend import ci_probe, duckyos_account, window_view  # noqa: F401 — same reason

TOKEN = "dky_v1_SECRET_test_token_value"
PC = {"keyId": "k_pc", "name": "Tas's PC", "live": True, "mine": False}


def _png(w: int = 64, h: int = 36) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _Link:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def list_duckies(self):
        return {"ok": True, "devices": [{"keyId": "k_mac", "name": "CI Mac", "live": True, "mine": True}, PC]}

    def call(self, to, method, args=None, *, timeout=30.0):
        self.calls.append((to, method))
        return self.answer


GOOD = {"ok": True, "result": {
    "ping": {"ok": True, "result": {"status": "ok"}},
    "verse": {"ok": True, "num_errors": 0},
    "screenshot": {"ok": True, "size": "64x36", "png_b64": base64.b64encode(_png()).decode("ascii")},
}}


@pytest.fixture
def runner(monkeypatch, tmp_path):
    from backend.agent import secrets
    from frontend import ducky_link

    for name in ("get_key", "get_key_fresh", "set_key", "clear_key"):
        monkeypatch.setattr(secrets, name, getattr(secrets, name))  # the probe swaps them: undone after
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DUCKY_SANDBOX", "1")
    monkeypatch.setenv(ci_probe.TOKEN_ENV, json.dumps({"key_id": "k_mac", "token": TOKEN, "email": "ci@example.test",
                                                       "base_url": "https://store.example.test"}))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ducky_link, "list_duckies", lambda: _Link(None).list_duckies())
    return tmp_path


def _no_token_anywhere(out, capsys) -> None:
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err
    for f in out.rglob("*"):
        assert not f.is_file() or TOKEN.encode() not in f.read_bytes(), f


def test_the_pc_runs_ping_verse_and_a_screenshot(runner, capsys) -> None:
    import os

    from backend.agent import secrets

    link = _Link(GOOD)
    assert ci_probe.remote_uefn(str(runner), link=link) == 0
    report = json.loads((runner / "remote.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["failed"] == []
    assert report["pc"] == {"key_id": "k_pc", "name": "Tas's PC"}
    assert report["ping"]["ok"] and report["verse"] == {"ok": True, "num_errors": 0}
    assert report["screenshot"] == {"ok": True, "size": "64x36", "file": "remote-screenshot.png"}
    assert (runner / "remote-screenshot.png").read_bytes() == _png()
    assert report["handoff"]["open_remote"] == [{"key_id": "k_pc", "name": "Tas's PC"}]
    assert link.calls == [("k_pc", ci_probe.REMOTE_CHECK)]
    assert ci_probe.TOKEN_ENV not in os.environ  # emptied: no child process inherits it
    assert json.loads(secrets.get_key("duckyos_account"))["device_key"] == TOKEN  # held in this process only
    printed = capsys.readouterr().out
    assert "Open Tas's PC" in printed
    assert TOKEN not in printed
    _no_token_anywhere(runner, capsys)


@pytest.mark.parametrize("answer, why", [
    ({"ok": False, "error": "That UEFN Ducky isn't answering."}, "isn't answering"),
    ({"ok": True, "result": {**GOOD["result"], "verse": {"ok": False, "error": "Workflow Server not connected"}}}, "verse"),
    ({"ok": True, "result": {**GOOD["result"], "screenshot": {"ok": False, "error": "listener offline"}}}, "screenshot"),
])
def test_any_failure_exits_1_and_says_why(runner, capsys, answer, why) -> None:
    assert ci_probe.remote_uefn(str(runner), link=_Link(answer)) == 1
    report = (runner / "remote.json").read_text(encoding="utf-8")
    assert json.loads(report)["ok"] is False and why in report
    _no_token_anywhere(runner, capsys)


def test_the_first_call_pins_the_pc_then_the_check_runs(runner, capsys) -> None:
    class _Pairs(_Link):
        def call(self, to, method, args=None, *, timeout=30.0):
            self.calls.append((to, method))
            return {"ok": False, "pair": True, "error": "Approve this PC (code ABC123)"} if len(self.calls) == 1 else GOOD

    link = _Pairs(None)
    assert ci_probe.remote_uefn(str(runner), link=link) == 0
    assert len(link.calls) == 2
    assert "ABC123" in json.loads((runner / "remote.json").read_text(encoding="utf-8"))["pair"]


def test_off_a_ci_runner_it_never_signs_in(runner, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS")
    link = _Link(GOOD)
    assert ci_probe.remote_uefn(str(runner), link=link) == 1
    assert "only on a CI runner" in (runner / "remote.json").read_text(encoding="utf-8") and link.calls == []


def test_a_bad_token_is_refused_without_echoing_it(runner, monkeypatch, capsys) -> None:
    monkeypatch.setenv(ci_probe.TOKEN_ENV, TOKEN)  # bare, not the JSON
    assert ci_probe.remote_uefn(str(runner), link=_Link(GOOD)) == 1
    assert "not the JSON" in (runner / "remote.json").read_text(encoding="utf-8")
    _no_token_anywhere(runner, capsys)


def test_a_big_capture_shrinks_under_the_sealed_limit() -> None:
    import os

    from PIL import Image

    noisy = Image.frombytes("RGB", (1920, 1080), os.urandom(1920 * 1080 * 3))
    buf = io.BytesIO()
    noisy.save(buf, format="PNG")
    png, size = ci_probe._small_png(buf.getvalue())
    assert len(png) <= ci_probe._SHOT_MAX or size.startswith("80x")
    assert Image.open(io.BytesIO(png)).format == "PNG"


def test_the_pc_answers_the_check_through_its_mailbox(monkeypatch) -> None:
    from frontend import duckyos_account

    monkeypatch.setattr(ci_probe, "uefn_check", lambda: {"ping": {"ok": True}})
    assert ci_probe.REMOTE_CHECK in duckyos_account.RPC_ALLOWLIST
    assert ci_probe.REMOTE_CHECK not in duckyos_account.WEBSITE_RPC  # only a paired Ducky, never the site
    assert duckyos_account.dispatch_desktop_rpc(ci_probe.REMOTE_CHECK, {"anything": 1}) == {
        "ok": True, "result": {"ping": {"ok": True}}}


def test_uefn_check_reports_each_step(monkeypatch) -> None:
    from backend.tools.core import system
    from backend.tools.verse import verse_diagnostics

    monkeypatch.setattr(system, "ping", lambda: json.dumps({"status": "ok"}))
    monkeypatch.setattr(verse_diagnostics, "workspace_compile_verse",
                        lambda: json.dumps({"compile": {"numErrors": 2}, "diagnostics": {}}))
    monkeypatch.setattr(ci_probe, "_screenshot", lambda: (_ for _ in ()).throw(TimeoutError("UEFN busy")))
    got = ci_probe.uefn_check()
    assert got["ping"] == {"ok": True, "result": {"status": "ok"}}
    assert got["verse"] == {"ok": True, "num_errors": 2}
    assert got["screenshot"] == {"ok": False, "error": "TimeoutError: UEFN busy"}
