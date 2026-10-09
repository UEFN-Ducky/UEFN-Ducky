"""Crash reports: after a launch that crashed, the next one asks the user to send what happened."""

from __future__ import annotations

import faulthandler
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from frontend import boot_report


@pytest.fixture
def crash(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(boot_report, "_state", {})
    # Keep pytest's own crash handler in this process; the child process below uses the real one.
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **k: None)
    monkeypatch.setattr(faulthandler, "disable", lambda *a, **k: None)
    calls = {"asked": [], "sent": [], "answer": True, "online": True}

    def ask(body):
        calls["asked"].append(body)
        return calls["answer"]

    def send(body, version):
        if calls["online"]:
            calls["sent"].append(body)
        return calls["online"]

    monkeypatch.setattr(boot_report, "_ask_to_send", ask)
    monkeypatch.setattr(boot_report, "_send", send)
    return calls


def _crashed_launch(*stages: str) -> None:
    boot_report.begin("1.2.360")
    for name in stages:
        boot_report.stage(name)
    # ...the process dies here: no window, no clean exit.


def test_after_a_crash_the_user_is_asked_and_the_report_is_sent_only_on_send(crash) -> None:
    _crashed_launch("import_panel_api", "create_window")
    boot_report.begin("1.2.360")
    assert len(crash["asked"]) == 1 and len(crash["sent"]) == 1
    report = crash["sent"][0]
    assert report["source"] == "crash" and report["formId"] == "uefn-ducky-feedback"
    assert "closed while starting, last stage reached: create_window" in report["message"]
    assert "import_panel_api" in report["error_log"]
    assert [r["sent"] for r in boot_report.list_reports()] == [True]
    boot_report.mark("ok")
    boot_report.begin("1.2.360")
    assert len(crash["asked"]) == 1  # a good launch asks nothing


def test_dont_send_sends_nothing_and_keeps_the_report_for_support(crash) -> None:
    crash["answer"] = False
    _crashed_launch("create_window")
    boot_report.begin("1.2.360")
    assert crash["sent"] == []
    (kept,) = boot_report.list_reports()
    assert kept["sent"] is False and "create_window" in kept["text"]
    # Settings → Support: Send, then it shows as sent.
    assert boot_report.send_report(kept["id"], "1.2.360")
    assert [r["sent"] for r in boot_report.list_reports()] == [True]
    assert boot_report.delete_report(boot_report.list_reports()[0]["id"])
    assert boot_report.list_reports() == []


def test_one_crash_is_asked_about_once_even_if_the_next_launch_fails_too(crash) -> None:
    _crashed_launch("create_window")
    boot_report.begin("1.2.360")  # asks about the first crash, then this launch also dies
    boot_report.begin("1.2.360")  # asks about the second crash only
    assert len(crash["asked"]) == 2
    assert crash["asked"][0] is not crash["asked"][1]


def test_clean_exits_and_handoffs_ask_nothing(crash) -> None:
    for end in ("closed", "handoff"):
        boot_report.begin("1.2.360")
        boot_report.mark(end)
    boot_report.begin("1.2.360")
    assert crash["asked"] == []


def test_a_startup_error_survives_the_exit_that_follows_it(crash) -> None:
    boot_report.begin("1.2.360")
    boot_report.mark("fatal", error="RuntimeError: panel build incomplete")
    boot_report.mark("closed")
    boot_report.begin("1.2.360")
    assert crash["asked"] and "startup error" in crash["asked"][0]["message"]
    assert "panel build incomplete" in crash["asked"][0]["error_log"]


def test_offline_send_keeps_the_report(crash) -> None:
    crash["online"] = False
    _crashed_launch("create_window")
    boot_report.begin("1.2.360")
    assert [r["sent"] for r in boot_report.list_reports()] == [False]


def test_the_report_names_the_loading_plugin_and_hides_the_home_folder(crash) -> None:
    boot_report.begin("1.2.360")
    pid = boot_report._state["pid"]
    markers = boot_report._dir().parent / "uefn_plugins" / ".loading"
    markers.mkdir(parents=True)
    (markers / f"account.{pid}.json").write_text(json.dumps({"plugin": "account", "version": "1.0.50"}), encoding="utf-8")
    home = str(Path.home())
    boot_report.mark("fatal", error=f"ImportError: DLL load failed in {home}\\AppData\\Local\\x.pyd")
    boot_report.begin("1.2.360")
    body = crash["asked"][0]
    assert "Loading when it closed: account 1.0.50" in body["error_log"]
    assert home not in body["error_log"]
    why = boot_report.why_summary(body)
    assert "It was loading the plugin account 1.0.50." in why and "ImportError" in why


@pytest.mark.skipif(sys.platform != "win32", reason="native crash path is Windows")
def test_a_real_native_crash_is_reported_with_its_python_stack(crash, tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        f"""
        import sys, ctypes
        sys.path.insert(0, {str(repo)!r})
        from frontend import boot_report
        boot_report.begin("1.2.360")
        boot_report.stage("load_plugins")
        def load_bad_plugin():
            ctypes.string_at(0)  # access violation, like a compiled plugin crashing
        load_bad_plugin()
        """
    )
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=60)
    assert proc.returncode != 0
    boot_report.begin("1.2.360")
    assert len(crash["asked"]) == 1
    body = crash["asked"][0]
    assert "crashed while starting (last stage: load_plugins)" in body["message"]
    assert "access violation" in body["error_log"].lower()
    assert "load_bad_plugin" in body["error_log"]
    assert "access violation" in boot_report.why_summary(body).lower()
