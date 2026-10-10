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
    monkeypatch.setattr(boot_report, "_pc_restarted_since", lambda when: calls.get("restarted", False))
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
    boot_report.mark("closed")
    boot_report.begin("1.2.360")
    assert len(crash["asked"]) == 1  # a good launch asks nothing


def test_a_running_ducky_that_vanished_without_a_trace_is_reported(crash) -> None:
    # A fail-fast crash, WebView2 taking the process down, or Task Manager: the window
    # was up, nothing exited on purpose, and Python wrote no crash dump.
    _crashed_launch("create_window", "window_shown")
    boot_report.mark("ok")
    boot_report.begin("1.2.360")
    (body,) = crash["asked"]
    assert "closed by itself while running" in body["message"]
    assert "window_shown" in body["error_log"]
    assert boot_report.NO_TRACE in boot_report.why_summary(body)


def test_a_launch_the_pc_restart_ended_is_not_a_crash(crash) -> None:
    _crashed_launch("window_shown")
    boot_report.mark("ok")
    crash["restarted"] = True
    boot_report.begin("1.2.360")
    assert crash["asked"] == []


@pytest.mark.skipif(sys.platform != "win32", reason="boot time is read on Windows")
def test_restart_check_compares_against_windows_boot_time() -> None:
    import time

    assert boot_report._pc_restarted_since(1.0)  # Windows booted after 1970
    assert not boot_report._pc_restarted_since(time.time())


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


def _dump(current_at: int | None, threads: int = 30) -> str:
    blocks = ["Windows fatal exception: access violation"]
    for n in range(threads):
        head = "Current thread" if n == current_at else "Thread"
        frames = "\n".join(f'  File "backend\\worker_{n}.py", line {k} in step_{k}' for k in range(8))
        blocks.append(f"{head} 0x{n:08x} (most recent call first):\n{frames}")
    blocks.append('Thread 0x00000d50 (most recent call first):\n  File "launcher.py", line 408 in main')
    return "\n\n".join(blocks) + "\n"


def test_a_long_crash_dump_keeps_the_fault_line_and_the_crashing_thread() -> None:
    text = boot_report.trim_native(_dump(current_at=2))
    assert len(text) <= boot_report.NATIVE_MAX
    assert text.startswith("Windows fatal exception: access violation")
    assert "Current thread 0x00000002" in text and "worker_2.py" in text
    assert text.rstrip().endswith('line 408 in main')
    assert "more threads not shown" in text


def test_a_crash_outside_python_threads_says_so() -> None:
    text = boot_report.trim_native(_dump(current_at=None))
    assert "runs no Python code" in text
    assert text.splitlines()[0].startswith("(The crash was in a thread")


def test_only_the_last_dump_of_a_launch_is_reported() -> None:
    text = boot_report.trim_native(_dump(current_at=1, threads=2) + "\n" + _dump(current_at=0, threads=2))
    assert "1 earlier exception in this launch not shown" in text
    assert text.count("Windows fatal exception") == 1 and "Current thread 0x00000000" in text


@pytest.mark.skipif(sys.platform != "win32", reason="native crash path is Windows")
def test_a_native_crash_in_a_worker_thread_is_named_even_with_many_threads(crash, tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        f"""
        import sys, ctypes, threading, time
        sys.path.insert(0, {str(repo)!r})
        from frontend import boot_report
        boot_report.begin("1.2.361")
        boot_report.stage("panel_api_init")
        def a_busy_background_thread_with_a_long_name(depth):
            if depth:
                return a_busy_background_thread_with_a_long_name(depth - 1)
            time.sleep(60)
        for _ in range(24):
            threading.Thread(target=a_busy_background_thread_with_a_long_name, args=(12,), daemon=True).start()
        time.sleep(0.3)
        def import_compiled_plugin_backend():
            import faulthandler
            faulthandler._read_null()  # a real crash: ctypes would turn an access violation into OSError
        threading.Thread(target=import_compiled_plugin_backend).start()
        time.sleep(30)
        """
    )
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=90)
    assert proc.returncode != 0
    boot_report.begin("1.2.361")
    body = crash["asked"][0]
    log = body["error_log"]
    assert len(log) <= boot_report.FIELD_MAX
    assert "crashed while starting (last stage: panel_api_init)" in log
    assert "Windows fatal exception: access violation" in log
    assert "Current thread" in log and "import_compiled_plugin_backend" in log
    assert "more threads not shown" in log


@pytest.mark.skipif(sys.platform != "win32", reason="process check is Windows")
def test_a_launch_that_is_still_starting_is_not_reported_as_a_crash(crash, tmp_path: Path) -> None:
    slow = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        record = {"version": "1.2.361", "state": "starting", "pid": slow.pid, "started": 1.0, "stages": [["panel_api_init", 3.1]]}
        folder = tmp_path / "UEFN-Ducky" / "crash"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "boot.json").write_text(json.dumps(record), encoding="utf-8")
        boot_report.begin("1.2.361")  # opened again while the first one is still starting
        assert crash["asked"] == []
    finally:
        slow.kill()
        slow.wait()
    (folder / "boot.json").write_text(json.dumps(record), encoding="utf-8")
    boot_report.begin("1.2.361")  # that process is gone without finishing: now it is a crash
    assert len(crash["asked"]) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="native crash path is Windows")
@pytest.mark.parametrize("state", ["starting", "ok"])
def test_second_launch_preserves_running_launch_until_its_crash(crash, tmp_path, state):
    repo = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(f"""
        import sys, faulthandler
        sys.path.insert(0, {str(repo)!r})
        from frontend import boot_report
        boot_report.begin("launch-A")
        boot_report.stage("A_panel_api")
        boot_report.stage("A_window")
        boot_report.mark({state!r})
        boot_report._native_file.write("A native log before handoff\\n")
        boot_report._native_file.flush()
        print("ready", flush=True)
        sys.stdin.readline()
        faulthandler._read_null()
    """)
    proc = subprocess.Popen([sys.executable, "-c", script],
        env=dict(os.environ, LOCALAPPDATA=str(tmp_path)), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        boot_before = boot_report._boot_path().read_bytes()
        native_before = boot_report._native_path().read_bytes()
        boot_report.begin("launch-B")
        boot_report.stage("B_handoff")
        boot_report.mark("handoff")
        boot_report.mark("closed")
        assert crash["asked"] == []
        assert boot_report._boot_path().read_bytes() == boot_before
        assert boot_report._native_path().read_bytes() == native_before
        proc.communicate("crash\n", timeout=30)
        assert proc.returncode != 0
        boot_report.begin("launch-C")
        assert len(crash["asked"]) == 1
        report = crash["asked"][0]
        assert report["app_version"] == "launch-A"
        assert "A_panel_api" in report["error_log"] and "A_window" in report["error_log"]
        assert "B_handoff" not in report["error_log"]
        assert "access violation" in report["error_log"].lower()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)
