"""Outside-agent terminal calls reach the window and retain approval semantics."""
import io
import json
import threading
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.tools.panel import ducky_panel as tools
from backend.tools.panel import permission_prompt as permissions
from frontend.ui_web import agent_modes, terminal
from frontend.ui_web.panel_api import PanelApi
from frontend.ui_web.terminal.manager import TerminalManager


@pytest.fixture
def forwarded(monkeypatch):
    mgr = TerminalManager()
    session = Mock(cwd="C:/repo", shell="powershell")
    session.is_busy.return_value = False
    session.is_alive.return_value = True
    session.run_command.return_value = {"ok": True, "exit_code": 0, "output_tail": "done"}
    session.read_output_tail.return_value = "done"
    monkeypatch.setattr(mgr, "get_session", lambda sid: session if sid == "s1" else None)
    monkeypatch.setattr(mgr, "list_sessions", lambda: [{"session_id": "s1", "cwd": "C:/repo"}])
    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: mgr)
    monkeypatch.setattr(agent_modes, "get_panel_push", lambda: None)
    monkeypatch.setattr(tools, "tool_json", lambda value, **kw: json.dumps(value))
    monkeypatch.setattr(tools, "_terminal_chat", lambda conv: "bound-chat")
    monkeypatch.setattr(permissions, "allows_everything", lambda chat: False)
    monkeypatch.setattr(permissions, "note_shell_dir", lambda *a: None)
    from backend.automations import runner
    monkeypatch.setattr(runner, "typed_command_approved", lambda command: False)
    calls = []
    api = PanelApi()

    def urlopen(request, timeout):
        name = request.full_url.rsplit("/", 1)[1]
        args = json.loads(request.data)["args"]
        calls.append((name, args, timeout))
        result = getattr(api, name)(**args)
        return io.BytesIO(json.dumps({"ok": True, "result": result}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return SimpleNamespace(mgr=mgr, session=session, calls=calls, api=api)


def test_open_list_read_close_use_window(forwarded, monkeypatch):
    mgr = forwarded.mgr
    monkeypatch.setattr(mgr, "spawn", Mock(return_value={"ok": True, "session_id": "s1"}))
    monkeypatch.setattr(mgr, "kill", Mock(return_value={"ok": True}))
    assert json.loads(tools.ducky_terminal_open()) == {"ok": True, "session_id": "s1"}
    assert mgr.spawn.call_args.kwargs["push_open"] is True
    assert mgr.spawn.call_args.kwargs["conv_id"] == "bound-chat"
    assert json.loads(tools.ducky_terminal_list())["sessions"][0]["session_id"] == "s1"
    assert json.loads(tools.ducky_terminal_read_output(" s1 ", 1))["output"] == "done"
    forwarded.session.read_output_tail.assert_called_with(max_chars=500)
    assert json.loads(tools.ducky_terminal_close(" s1 "))["ok"]
    mgr.kill.assert_called_once_with("s1")
    assert [c[0] for c in forwarded.calls] == ["terminal_spawn", "terminal_list", "terminal_read_output", "terminal_kill"]


@pytest.mark.parametrize("approve", [True, False])
def test_popup_allow_and_deny_return_result(forwarded, approve):
    events = []
    def push(event):
        events.append(event)
        # Decide after the runner has begun waiting, just like the UI.
        def decide():
            if approve:
                forwarded.mgr.approve_command(event["request_id"])
            else:
                forwarded.mgr.reject_command(event["request_id"], "refused")
        threading.Timer(0.03, decide).start()
    forwarded.mgr.set_push(push)
    result = json.loads(tools.ducky_terminal_run("s1", "echo done"))
    assert events[0]["type"] == "terminal_command_pending"
    assert events[0]["conv_id"] == "bound-chat"
    assert result["ok"] is approve
    if approve:
        assert result["exit_code"] == 0 and result["output_tail"] == "done"
        forwarded.session.run_command.assert_called_once()
    else:
        assert result["error"] == "refused"
        forwarded.session.run_command.assert_not_called()
    assert forwarded.calls[-1][2] == 450.0


@pytest.mark.parametrize("background,wait", [(False, True), (True, True), (False, False)])
def test_allow_everything_and_background(forwarded, monkeypatch, background, wait):
    events = []
    forwarded.mgr.set_push(events.append)
    monkeypatch.setattr(permissions, "allows_everything", lambda chat: chat == "bound-chat")
    guard = Mock(return_value=None)
    monkeypatch.setattr(permissions, "_never_runs", guard)
    result = json.loads(tools.ducky_terminal_run(
        "s1", "echo done", background=background, wait=wait, command_timeout_s=180,
    ))
    assert result["ok"] and not events
    assert guard.call_args.args[1] == "C:/repo"
    assert forwarded.calls[-1][2] == (330.0 if wait and not background else 150.0)
    assert forwarded.calls[-1][1]["command_timeout_s"] == 180
    if background or not wait:
        assert result["status"] == "running"
        assert json.loads(tools.ducky_terminal_read_output("s1"))["output"] == "done"


def test_auto_approval_keeps_command_guard(forwarded, monkeypatch):
    monkeypatch.setattr(permissions, "allows_everything", lambda chat: True)
    monkeypatch.setattr(permissions, "_never_runs", lambda *args: "refused")
    assert json.loads(tools.ducky_terminal_run("s1", "blocked"))["error"] == "refused"
    assert [c[0] for c in forwarded.calls] == ["terminal_list"]


def test_window_unavailable_never_creates_local_terminal(forwarded, monkeypatch):
    def unavailable(*a, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    monkeypatch.setattr(terminal, "get_terminal_manager", Mock(side_effect=AssertionError("local terminal")))
    result = json.loads(tools.ducky_terminal_open())
    assert result["ok"] is False and "window terminal unavailable" in result["error"]


def test_window_process_uses_local_manager(forwarded, monkeypatch):
    push = Mock()
    monkeypatch.setattr(agent_modes, "get_panel_push", lambda: push)
    assert tools._terminal_manager() is forwarded.mgr
    assert forwarded.mgr._push is push


def test_existing_ui_request_still_queues(forwarded):
    events = []
    forwarded.mgr.set_push(events.append)
    result = forwarded.api.terminal_request_command("s1", "echo done")
    assert result["status"] == "pending_approval"
    assert events[0]["type"] == "terminal_command_pending"
    forwarded.session.run_command.assert_not_called()



def test_real_loopback_background_output_after_sixty_seconds(monkeypatch, tmp_path):
    """A long shell run survives the HTTP return and is readable through the window."""
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if sys.platform != "win32":
        pytest.skip("PowerShell terminal integration requires Windows")
    pytest.importorskip("winpty")
    mgr = TerminalManager()
    events = []
    mgr.set_push(events.append)
    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: mgr)
    monkeypatch.setattr(agent_modes, "get_panel_push", lambda: None)
    monkeypatch.setattr(tools, "tool_json", lambda value, **kw: json.dumps(value))
    monkeypatch.setattr(tools, "_terminal_chat", lambda conv: "test-chat")
    monkeypatch.setattr(permissions, "allows_everything", lambda chat: True)
    api = PanelApi()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["args"]
            result = getattr(api, self.path.rsplit("/", 1)[-1])(**args)
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setattr(tools, "PANEL_LISTENER_PORT", server.server_port + 1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opened = json.loads(tools.ducky_terminal_open(shell="powershell", cwd=str(tmp_path)))
        assert opened["ok"], opened
        sid = opened["session_id"]
        assert events[0]["type"] == "terminal_open"
        assert json.loads(tools.ducky_terminal_list())["sessions"][0]["session_id"] == sid
        started = time.monotonic()
        result = json.loads(tools.ducky_terminal_run(
            sid, "Start-Sleep -Seconds 61; Write-Output ('DUCKY_LONG_' + 'FINISHED')", background=True,
        ))
        assert result["ok"] and result["status"] == "running", result
        assert time.monotonic() - started < 10
        deadline = started + 90
        while time.monotonic() < deadline:
            output = json.loads(tools.ducky_terminal_read_output(sid))
            assert output["ok"], output
            if "DUCKY_LONG_FINISHED" in output["output"]:
                break
            time.sleep(1)
        else:
            pytest.fail(f"Missing long-run output: {output}")
        assert time.monotonic() - started > 60
        assert not [e for e in events if e["type"] == "terminal_command_pending"]
        assert json.loads(tools.ducky_terminal_close(sid))["ok"]
    finally:
        mgr.shutdown_all()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
