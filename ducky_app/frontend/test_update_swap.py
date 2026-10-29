"""Ducky switches to a downloaded version only when that can't interrupt anyone."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.automations import scheduler
from frontend import app_versions, update_swap as us

_real_due_within = scheduler.due_within


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """Nothing running unless a test says so; one main window, idle for an hour."""
    monkeypatch.setattr("frontend.ui_web.agent_modes.list_running_agents", lambda: [])
    monkeypatch.setattr("backend.automations.live_runs.runs", lambda workflow_id="": [])
    monkeypatch.setattr("backend.automations.scheduler.due_within", lambda seconds, now=None: [])

    class _Terminals:
        sessions: list = []

        def list_sessions(self):
            return self.sessions

    terminals = _Terminals()
    monkeypatch.setattr("frontend.ui_web.terminal.manager.get_terminal_manager", lambda: terminals)
    monkeypatch.setattr("frontend.remote_direct.last_report", lambda: {})
    monkeypatch.setattr(us, "_ui", {})
    monkeypatch.setattr(us, "_holders", {"window_holder": {"hidden": False}})
    monkeypatch.setattr(us, "_state", {"phase": "idle", "version": "", "done": 0, "total": 0, "error": "", "failures": 0})
    monkeypatch.setattr(us, "_started_at", time.time() - 3600)
    us.report_ui({"window": "main", "visible": True, "idle_s": 3600, "dirty": 0, "jobs": []})
    return terminals


def test_idle_when_nothing_runs_and_nobody_used_ducky_for_a_while() -> None:
    assert us.idle_reasons() == []


def test_each_kind_of_work_holds_the_switch(monkeypatch, _quiet) -> None:
    # A plain chat carries over (the switch stops it, the new version continues it); a team chat can't.
    monkeypatch.setattr("frontend.ui_web.agent_modes.list_running_agents", lambda: ["a", "team"])
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation_meta", lambda cid, root=None: SimpleNamespace(is_group=cid == "team"))
    monkeypatch.setattr("backend.automations.live_runs.runs", lambda workflow_id="": [{"state": "started"}])
    monkeypatch.setattr("backend.automations.scheduler.due_within", lambda seconds, now=None: ["Daily island check"])
    _quiet.sessions = [{"id": "t1"}]
    monkeypatch.setattr("frontend.remote_direct.last_report", lambda: {"state": "connected", "at": time.time()})
    us.report_ui({"window": "main", "visible": True, "idle_s": 3600, "dirty": 1, "jobs": ["Render trailer"]})
    us.report_ui({"window": "focus-1", "visible": True, "idle_s": 3600, "dirty": 2, "jobs": []})
    reasons = us.idle_reasons()
    assert reasons == [
        "1 team chat running",
        "a workflow running",
        "1 terminal open",
        "3 unsaved files",
        "Render trailer",
        "Daily island check starting soon",
        "a phone or another PC connected",
    ]


def test_right_after_a_start_using_ducky_does_not_hold_it(monkeypatch, _quiet) -> None:
    monkeypatch.setattr(us, "_started_at", time.time())
    us.report_ui({"window": "main", "visible": True, "idle_s": 1, "dirty": 0, "jobs": []})
    assert us.idle_reasons() == []
    # Real work still holds it.
    _quiet.sessions = [{"id": "t1"}]
    assert us.idle_reasons() == ["1 terminal open"]


def test_repeated_download_failures_bring_back_setup(monkeypatch) -> None:
    from frontend import version_check

    update = {"version": "9.9.9", "packUrl": "/p", "manifestUrl": "/m", "manifestSha256": "a" * 64}
    monkeypatch.setattr("frontend.version_check.fetch_remote_payload", lambda: ({"update": update}, None))

    def _fail(u, base_url, progress=None):
        raise RuntimeError("blocked")

    monkeypatch.setattr("frontend.app_stage.stage", _fail)
    us.check_and_stage()
    assert not version_check._background_update_stuck()
    us.check_and_stage()
    assert version_check._background_update_stuck()
    monkeypatch.setattr("frontend.app_stage.stage", lambda u, base_url, progress=None: None)
    monkeypatch.setattr("frontend.app_stage.prune", lambda running="": [])
    us.check_and_stage()
    assert not version_check._background_update_stuck()


def test_someone_using_ducky_holds_it_unless_the_window_is_hidden(monkeypatch) -> None:
    us.report_ui({"window": "main", "visible": True, "idle_s": 30, "dirty": 0, "jobs": []})
    assert us.idle_reasons() == ["you, using Ducky"]
    us.report_ui({"window": "main", "visible": False, "idle_s": 30, "dirty": 0, "jobs": []})
    assert us.idle_reasons() == []
    monkeypatch.setattr(us, "_holders", {"window_holder": {"hidden": True}})
    us.report_ui({"window": "main", "visible": True, "idle_s": 30, "dirty": 0, "jobs": []})
    assert us.idle_reasons() == []


def test_a_window_that_stopped_answering_counts_as_busy(monkeypatch) -> None:
    us.report_ui({"window": "main", "visible": True, "idle_s": 3600})
    later = time.time() + us.UI_REPORT_STALE_S + 5
    assert us.idle_reasons(later) == ["Ducky's window (not answering)"]
    # Hidden in the tray, the window isn't needed to vouch.
    monkeypatch.setattr(us, "_holders", {"window_holder": {"hidden": True}})
    assert us.idle_reasons(later) == []


def test_update_now_refuses_while_work_would_be_cut_off(monkeypatch, _quiet) -> None:
    assert us.apply_now() == {"ok": False, "error": "No update is ready yet."}
    us._set(phase="ready", version="1.2.412")
    _quiet.sessions = [{"id": "t1"}]
    got = us.apply_now()
    assert got["ok"] is False and got["waiting_for"] == ["1 terminal open"]
    # A running chat (it carries over) and someone at the keyboard are fine: they asked for it.
    _quiet.sessions = []
    monkeypatch.setattr("frontend.ui_web.agent_modes.list_running_agents", lambda: ["a"])
    us.report_ui({"window": "main", "visible": True, "idle_s": 1})
    called = []
    monkeypatch.setattr(us, "switch", lambda version: called.append(version) or {"ok": True})
    assert us.apply_now() == {"ok": True}
    assert called == ["1.2.412"]


def test_switch_starts_the_new_version_and_leaves_without_stopping_bridges(monkeypatch) -> None:
    folder = app_versions.version_dir("1.2.412")
    folder.mkdir(parents=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / app_versions.READY_MARKER).write_text("{}", encoding="utf-8")

    class Api:
        _maximized = True

    monkeypatch.setattr(us, "_holders", {"api": Api(), "window_holder": {"hidden": True}})
    started, exits, stopped = [], [], []
    monkeypatch.setattr(app_versions, "start_detached", lambda exe, args, env=None: started.append((exe, args, env)) or True)
    monkeypatch.setattr("frontend.ui_web.shutdown.hard_exit", lambda api=None, keep_others=False: exits.append(keep_others))
    monkeypatch.setattr("frontend.usage_stats.app_closing", lambda: None)
    # A chat mid-turn is stopped (not by the user) and handed to the new version.
    monkeypatch.setattr("frontend.ui_web.agent_modes.list_running_agents", lambda: ["c1"])
    monkeypatch.setattr("frontend.ui_web.agent_modes.running_turn_settings", lambda cid: {"mode": "agent", "model": "ducky-ai"})
    monkeypatch.setattr("frontend.ui_web.agent_modes.cancel_agent", lambda cid, by_user=True: stopped.append((cid, by_user)))
    monkeypatch.setattr("frontend.ui_web.agent_modes.is_agent_running", lambda cid: False)

    assert us.switch("1.2.412") == {"ok": True}
    exe, args, env = started[0]
    assert exe == folder / "UEFN-Ducky.exe"
    assert args[0] == "--after-update" and args[1].isdigit()
    assert env[us.SWITCHED_FROM_ENV]
    assert exits == [True]
    assert stopped == [("c1", False)]
    handover = app_versions.take_handover()
    assert handover["hidden"] is True and handover["maximized"] is True
    assert handover["chats"] == [{"conv_id": "c1", "mode": "agent", "model": "ducky-ai"}]


def test_the_new_version_continues_the_chats_the_switch_stopped(monkeypatch) -> None:
    ran = []
    monkeypatch.setattr("frontend.ui_web.agent_modes.get_panel_push", lambda: print)
    monkeypatch.setattr(
        "frontend.ui_web.agent_modes.run_message",
        lambda cid, text, mode, model, resume=False: ran.append((cid, text, mode, model, resume)) or "run-1",
    )
    monkeypatch.setattr(us.threading, "Thread", lambda target, name, daemon: SimpleNamespace(start=target))
    us.continue_chats([{"conv_id": "c1", "mode": "agent", "model": "ducky-ai"}, {"bad": 1}])
    us.continue_chats(None)  # a handover without chats
    assert ran == [("c1", "", "agent", "ducky-ai", True)]


def test_switch_refuses_a_version_that_is_not_ready_or_bad(monkeypatch) -> None:
    assert us.switch("1.2.499")["ok"] is False
    folder = app_versions.version_dir("1.2.412")
    folder.mkdir(parents=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / app_versions.READY_MARKER).write_text("{}", encoding="utf-8")
    app_versions.mark_bad("1.2.412", "crashed")
    assert us.switch("1.2.412")["ok"] is False


def test_the_new_version_says_it_was_updated_for_a_while(monkeypatch) -> None:
    monkeypatch.setenv(us.SWITCHED_FROM_ENV, "1.2.410")
    monkeypatch.setattr(us, "_started_at", time.time())
    got = us.status()
    assert got["phase"] == "updated" and got["previous"] == "1.2.410"
    monkeypatch.setattr(us, "_started_at", time.time() - us.UPDATED_NOTE_S - 1)
    assert us.status()["phase"] == "idle"


def test_ready_status_lists_what_it_waits_for(monkeypatch) -> None:
    us._set(phase="ready", version="1.2.412")
    us.report_ui({"window": "main", "visible": True, "idle_s": 1})
    assert us.status()["waiting_for"] == ["you, using Ducky"]


def test_check_and_stage_gets_the_feeds_version_ready(monkeypatch) -> None:
    update = {"version": "9.9.9", "packUrl": "/p", "manifestUrl": "/m", "manifestSha256": "a" * 64}
    monkeypatch.setattr("frontend.version_check.fetch_remote_payload", lambda: ({"update": update}, None))
    staged = []
    monkeypatch.setattr("frontend.app_stage.stage", lambda u, base_url, progress=None: staged.append(u["version"]))
    monkeypatch.setattr("frontend.app_stage.prune", lambda running="": [])
    assert us.check_and_stage() == update
    assert staged == ["9.9.9"] and us.status()["phase"] == "ready"

    def _fail(u, base_url, progress=None):
        raise RuntimeError("network down")

    monkeypatch.setattr("frontend.app_stage.stage", _fail)
    assert us.check_and_stage() is None
    assert us.status()["phase"] == "failed" and "network down" in us.status()["error"]

    monkeypatch.setattr("frontend.version_check.fetch_remote_payload", lambda: ({"downloadsPaused": True}, None))
    assert us.check_and_stage() is None and us.status()["phase"] == "idle"


def test_due_within_sees_interval_and_cron_workflows(monkeypatch) -> None:
    now = datetime(2026, 10, 20, 7, 57).timestamp()
    flows = [
        {"id": "a", "name": "Every hour", "enabled": True, "last_run": now - 3500,
         "graph": {"nodes": [{"type": "start.cron", "config": {"interval_seconds": 3600}}]}},
        {"id": "b", "name": "Daily island check", "enabled": True, "last_run": now - 86000,
         "graph": {"nodes": [{"type": "start.cron", "config": {"cron": "0 8 * * *"}}]}},
        {"id": "c", "name": "Off", "enabled": False, "last_run": 0,
         "graph": {"nodes": [{"type": "start.cron", "config": {"interval_seconds": 60}}]}},
        {"id": "d", "name": "Later", "enabled": True, "last_run": now,
         "graph": {"nodes": [{"type": "start.cron", "config": {"interval_seconds": 3600}}]}},
    ]
    monkeypatch.setattr(scheduler, "all_workflows", lambda: flows)
    monkeypatch.setattr(scheduler, "runs_here", lambda wf: True)
    assert _real_due_within(300, now) == ["Every hour", "Daily island check"]


def test_hard_exit_for_a_switch_leaves_other_ducky_processes_alone(monkeypatch) -> None:
    from frontend.ui_web import shutdown

    calls = []
    monkeypatch.setattr(shutdown, "_exit_started", False)
    for name in ("_stop_all_agents", "_stop_cpu_workers", "_stop_all_terminals", "_flush_space_sync", "_stop_mcp_plugins"):
        monkeypatch.setattr(shutdown, name, lambda: None)
    monkeypatch.setattr(shutdown, "release_panel_process", lambda: None)
    monkeypatch.setattr(shutdown, "_kill_sibling_processes", lambda: calls.append("siblings"))
    monkeypatch.setattr(shutdown, "_spawn_kill_uefn_processes", lambda: calls.append("reaper"))
    monkeypatch.setattr(shutdown, "_terminate_process", lambda: calls.append("exit"))
    shutdown.hard_exit(keep_others=True)
    assert calls == ["exit"]
    monkeypatch.setattr(shutdown, "_exit_started", False)
    calls.clear()
    shutdown.hard_exit()
    assert calls == ["siblings", "reaper", "exit"]
