"""Windows Controlled folder access blocking Ducky or its agents: one named question, Windows'
admin prompt, a restart when Ducky itself was blocked."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from frontend import app_versions, folder_access as fa

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")

CLAUDE = r"C:\Users\pat\.local\bin\claude.exe"
NODE = r"C:\Program Files\nodejs\node.exe"
DUCKY_EXE = r"C:\Users\pat\AppData\Local\UEFN-Ducky\versions\app-1.2.493\UEFN-Ducky.exe"


@pytest.fixture
def run(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: True)
    monkeypatch.setattr(fa, "_state_path", lambda: tmp_path / fa.STATE_FILE)
    calls: dict = {"asked": [], "told": [], "allowed": [], "restarts": 0, "probes": 0, "modes": 0}
    world: dict = {"blocked": False, "mode": fa.BLOCK, "yes": True, "uac": True, "events": [], "restarted": True}

    def blocked() -> bool:
        calls["probes"] += 1
        return world["blocked"]

    def mode() -> int:
        calls["modes"] += 1
        return world["mode"]

    def check(probe: bool = True) -> str:
        def restart() -> bool:
            calls["restarts"] += 1
            return world["restarted"]

        return fa.check(
            probe=probe,
            blocked=blocked,
            mode=mode,
            events=lambda: list(world["events"]),
            ask=lambda text: calls["asked"].append(text) or world["yes"],
            tell=calls["told"].append,
            allow=lambda paths: calls["allowed"].append(paths) or world["uac"],
            restart=restart,
        )

    return check, calls, world


def test_nothing_blocked_asks_nothing(run) -> None:
    check, calls, _ = run
    assert check() == "ok"
    assert calls["asked"] == [] and calls["modes"] == 0  # PowerShell only after a refused write


def test_ducky_blocked_asks_once_allows_its_exes_and_restarts(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    assert check() == "allowed"
    assert calls["asked"] == [fa.ask_text([fa.DUCKY])] and "UEFN Ducky restarts" in calls["asked"][0]
    (paths,) = calls["allowed"]
    assert str(app_versions.versions_dir() / "*" / "UEFN-Ducky.exe") in paths  # every later version too
    assert calls["restarts"] == 1
    world["blocked"] = False  # restarted and allowed
    assert check() == "ok" and len(calls["asked"]) == 1


def test_an_agent_windows_blocked_is_named_and_allowed_without_a_restart(run) -> None:
    check, calls, world = run
    world["events"] = [("2026-11-11T08:00:00Z", CLAUDE)]
    assert check(probe=False) == "allowed"  # after a turn: the log only
    (text,) = calls["asked"]
    assert r"claude.exe (C:\Users\pat\.local\bin)" in text and "restarts" not in text
    assert calls["allowed"] == [[CLAUDE]] and calls["restarts"] == 0


def test_ducky_and_its_agents_in_one_question(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    world["events"] = [("2026-11-11T08:00:02Z", NODE), ("2026-11-11T08:00:01Z", CLAUDE),
                       ("2026-11-11T08:00:00Z", NODE)]
    assert check() == "allowed"
    assert len(calls["asked"]) == 1 and "UEFN Ducky, node.exe" in calls["asked"][0]
    (paths,) = calls["allowed"]
    assert paths[-2:] == [NODE, CLAUDE] and calls["restarts"] == 1


def test_ducky_named_in_the_log_needs_no_write_test(run) -> None:
    check, calls, world = run
    world["events"] = [("2026-11-11T08:00:00Z", DUCKY_EXE)]
    assert check() == "allowed"
    assert calls["probes"] == 0 and calls["modes"] == 0
    assert DUCKY_EXE not in calls["allowed"][0]  # Ducky's own paths, never the logged one


def test_a_block_already_handled_is_not_asked_about_again(run) -> None:
    check, calls, world = run
    world["events"] = [("2026-11-11T08:00:00Z", CLAUDE)]
    assert check(probe=False) == "allowed"
    assert check(probe=False) == "ok"  # the same log entry
    assert len(calls["asked"]) == 1


def test_no_holds_for_this_version_even_if_windows_blocks_it_again(run, monkeypatch) -> None:
    check, calls, world = run
    world["yes"] = False
    world["events"] = [("2026-11-11T08:00:00Z", CLAUDE)]
    assert check(probe=False) == "no"
    world["events"].append(("2026-11-11T09:00:00Z", CLAUDE))
    assert check(probe=False) == "ok" and len(calls["asked"]) == 1
    monkeypatch.setattr(fa, "__version__", "9.9.9")
    world["events"].append(("2026-11-11T10:00:00Z", CLAUDE))
    world["yes"] = True
    assert check(probe=False) == "allowed" and len(calls["asked"]) == 2


def test_still_blocked_after_allowing_says_how_to_do_it_by_hand_once(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    assert check() == "allowed"
    assert check() == "still_blocked"  # an organization's policy can override it
    assert calls["told"] == [fa.STILL_BLOCKED_TEXT]
    probes = calls["probes"]
    assert check() == "ok" and calls["probes"] == probes  # each probe also shows Windows' own notice
    assert len(calls["asked"]) == 1


def test_the_blocked_tool_server_counts_as_ducky_and_restarts_it(run) -> None:
    """The user report: UEFN-Ducky-Bridge.exe (the shared tool server) refused in Content\\Python."""
    check, calls, world = run
    bridge = r"C:\Users\pat\AppData\Local\Programs\UEFN Ducky\UEFN-Ducky-Bridge.exe"
    world["events"] = [("2026-11-11T08:00:00Z", bridge)]
    assert check(probe=False) == "allowed"
    assert calls["asked"] == [fa.ask_text([fa.DUCKY])]
    assert any(p.endswith("UEFN-Ducky-Bridge.exe") for p in calls["allowed"][0]) and calls["restarts"] == 1


def test_a_restart_that_cannot_start_tells_the_person_to_reopen(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    world["restarted"] = False
    assert check() == "allowed"
    assert calls["told"] == [fa.RESTART_TEXT]


def test_declining_windows_admin_prompt_keeps_ducky_running_and_says_how(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    world["uac"] = False
    assert check() == "failed"
    assert calls["told"] == [fa.NOT_ALLOWED_TEXT] and calls["restarts"] == 0


def test_a_refusal_that_is_not_controlled_folder_access_asks_nothing(run) -> None:
    check, calls, world = run
    world["blocked"] = True
    world["mode"] = 0
    assert check() == "ok"
    assert calls["asked"] == []


def test_source_runs_and_test_copies_skip_it(run, monkeypatch) -> None:
    check, calls, _ = run
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: False)
    assert check() == "skipped"
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: True)
    monkeypatch.setenv("DUCKY_SANDBOX", "1")
    assert check() == "skipped"
    assert calls["probes"] == 0


_EVENT = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><EventID>1123</EventID>"
          "<TimeCreated SystemTime='{when}'/></System><EventData><Data Name='Product Name'>Microsoft Defender"
          "</Data><Data Name='Path'>C:\\Users\\pat\\Documents\\x.verse</Data><Data Name='Process Name'>{proc}"
          "</Data></EventData></Event>")


def test_defenders_block_log_is_read_for_the_watched_programs_only(monkeypatch) -> None:
    xml = "".join(_EVENT.format(when=w, proc=p) for w, p in (
        ("2026-11-11T08:00:02.1Z", CLAUDE),
        ("2026-11-11T08:00:01.1Z", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        ("2026-11-11T08:00:00.1Z", r"C:\Tools\R&amp;D\Node.EXE"),
    ))
    seen: list = []

    def fake_run(cmd, **_k):
        seen.append(cmd)
        return SimpleNamespace(stdout=xml, returncode=0)

    monkeypatch.setattr(fa.subprocess, "run", fake_run)
    assert fa.blocked_programs() == [("2026-11-11T08:00:02.1Z", CLAUDE),
                                     ("2026-11-11T08:00:00.1Z", r"C:\Tools\R&D\Node.EXE")]
    assert seen[0][:3] == ["wevtutil", "qe", fa.LOG] and "EventID=1123" in seen[0][3]


def test_a_refused_write_names_controlled_folder_access_only_when_windows_logged_it(monkeypatch) -> None:
    island = r"C:\Users\pat\OneDrive\Documents\Fortnite Projects\abaa\Content\Python"
    logged = [("2026-11-11T08:00:00Z", r"C:\x\UEFN-Ducky-Bridge.exe", island + r"\.init_unreal.py.ab12.tmp")]
    turns: list = []
    monkeypatch.setattr(fa, "after_turn", lambda: turns.append(1))
    windows: list = []

    def blocks(window_ms: int):
        windows.append(window_ms)
        return logged

    assert fa.refusal_hint(island + r"\init_unreal.py", blocks=blocks) == fa.REFUSED_HINT
    assert fa.refusal_hint(island, blocks=blocks) == fa.REFUSED_HINT  # the folder itself
    assert windows[0] == fa.REFUSAL_WINDOW_S * 1000 and turns == [1, 1]  # the app asks right away
    assert fa.refusal_hint(r"C:\Users\pat\Desktop\x.txt", blocks=blocks) == ""  # folder permissions
    assert "Controlled folder access" in fa.REFUSED_HINT and "UEFN-Ducky-Bridge.exe" in fa.REFUSED_HINT


def test_ending_tool_servers_ends_only_ducky_daemons_named_in_their_state(monkeypatch, tmp_path: Path) -> None:
    from backend.util import proc_table

    me = os.getpid()

    def proc(pid: int, name: str, ppid: int = 4):
        return SimpleNamespace(pid=pid, ppid=ppid, name=name, started=10.0)

    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(proc_table, "table", lambda: {
        101: proc(101, "UEFN-Ducky-Bridge.exe"), 102: proc(102, "chrome.exe"), me: proc(me, "UEFN-Ducky.exe"),
        103: proc(103, "UEFN-Ducky.exe", ppid=me),
    })
    (tmp_path / "shared_mcp.json").write_text('{"pid": 101}', encoding="utf-8")
    (tmp_path / "shared_mcp-h0.json").write_text('{"pid": 102}', encoding="utf-8")  # pid reused by another program
    (tmp_path / "shared_mcp-h1.json").write_text(f'{{"pid": {me}}}', encoding="utf-8")  # the app hosts it
    (tmp_path / "shared_mcp-h3.json").write_text('{"pid": 103}', encoding="utf-8")  # the app's agent host
    (tmp_path / "shared_mcp-h2.json").write_text('{"pid": 999}', encoding="utf-8")  # already gone
    (tmp_path / "shared_mcp_fallbacks.json").write_text('{"pid": 101}', encoding="utf-8")  # not a daemon's state
    ended: list = []
    assert fa.end_tool_servers(terminate=lambda pid: ended.append(pid) or True) == [101]
    assert ended == [101]
    assert not (tmp_path / "shared_mcp.json").exists() and (tmp_path / "shared_mcp-h0.json").exists()


def test_the_write_test_covers_documents_and_every_projects_folder(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("frontend.ui_web.known_projects.documents_dir", lambda: tmp_path / "Documents")
    monkeypatch.setattr("frontend.ui_web.known_projects.known_project_roots", lambda: [
        str(tmp_path / "Documents" / "Fortnite Projects" / "abaa"),
        str(tmp_path / "Documents" / "Fortnite Projects" / "b"),
        str(tmp_path / "Desktop" / "Islands" / "c"),
    ])
    assert fa.probe_folders() == [tmp_path / "Documents", tmp_path / "Documents" / "Fortnite Projects",
                                  tmp_path / "Desktop" / "Islands"]


def test_a_device_path_in_the_log_becomes_a_drive_path() -> None:
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    assert ctypes.windll.kernel32.QueryDosDeviceW("C:", buf, len(buf))
    assert fa._dos_path(buf.value + r"\Tools\claude.exe") == r"C:\Tools\claude.exe"
    assert fa._dos_path(CLAUDE) == CLAUDE


def test_write_probe_leaves_nothing_and_reads_access_denied_as_blocked(tmp_path: Path, monkeypatch) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    assert fa.write_blocked(documents) is False
    assert list(documents.iterdir()) == []
    assert fa.write_blocked(tmp_path / "missing") is False  # not a block

    def refuse(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(fa.os, "open", refuse)
    assert fa.write_blocked(documents) is True


def test_admin_command_quotes_every_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(app_versions, "read_current", lambda: {"dir": str(tmp_path / "O'Brien" / "UEFN Ducky")})
    paths = fa.ducky_paths()
    assert str(tmp_path / "O'Brien" / "UEFN Ducky" / "UEFN-Ducky-Bridge.exe") in paths
    script = base64.b64decode(fa.allow_command(paths)).decode("utf-16-le")
    assert script.startswith("$ErrorActionPreference = 'Stop'; Add-MpPreference -ControlledFolderAccessAllowedApplications ")
    assert "'" + str(tmp_path / "O''Brien" / "UEFN Ducky" / "UEFN-Ducky.exe") + "'" in script
    assert "Set-MpPreference" not in script  # Set replaces the person's own allowed apps


def test_shells_and_script_hosts_are_never_offered() -> None:
    for exe in ("powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe", "python.exe", "wscript.exe"):
        assert exe not in fa.AGENT_PROGRAMS


def test_after_turn_checks_in_the_app_only_and_at_most_once_a_minute(monkeypatch) -> None:
    started: list = []
    monkeypatch.setattr(fa.threading, "Thread", lambda **k: SimpleNamespace(start=lambda: started.append(k["name"])))
    monkeypatch.setattr(fa, "_last_turn_check", [0.0])
    monkeypatch.setattr("backend.process_role.is_app", lambda: False)
    fa.after_turn()
    assert started == []
    monkeypatch.setattr("backend.process_role.is_app", lambda: True)
    fa.after_turn()
    fa.after_turn()
    assert started == ["folder-access-turn"]
