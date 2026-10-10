"""Tests must never send fixture events, runs or messages to the owner's panel."""
from __future__ import annotations

import io
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_all_panel_clients_use_the_isolated_port(monkeypatch):
    from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
    from frontend.ui_web import agent_modes
    from backend.panel import rpc
    from backend.agent import a2a_client

    assert PANEL_LISTENER_PORT == int(os.environ["UEFN_DUCKY_PANEL_PORT"])
    assert PANEL_LISTENER_PORT not in (4200, 4201)
    assert PanelSettings().port == PANEL_LISTENER_PORT
    calls = []

    def capture(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(b'{"ok":true,"result":{"ok":true,"value":{}}}')

    monkeypatch.setattr(agent_modes._urlreq, "urlopen", capture)
    monkeypatch.setattr(agent_modes, "_panel_push", None)
    events = queue.Queue()
    monkeypatch.setattr(agent_modes, "_forward_queue", events)
    monkeypatch.setattr(agent_modes, "_ensure_forwarder", lambda: None)
    agent_modes._forward_to_panel({"type": "workflow_run", "name": "guard"})
    # Stop the infinite sender after it has posted exactly one queued batch.
    get = events.get
    monkeypatch.setattr(events, "get", lambda block=True, timeout=None: get(block=False))
    with pytest.raises(queue.Empty):
        agent_modes._forward_sender()
    agent_modes._post_panel_run({"conv_id": "guard"}, http_timeout=0.1)
    rpc.panel_rpc("guard", timeout=0.1)
    a2a_client.send(sender="guard", to="fake", message="guard", expect_reply=False)
    base = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}"
    assert calls == [base + path for path in (
        "/__panel_event", "/__panel_run", "/__panel_rpc", "/__panel_api/agent_broker_call",
    )]


@pytest.mark.parametrize("override,expected", [(None, 4200), ("51234", 51234)])
def test_app_default_and_explicit_override(override, expected):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "ducky_app"))
    env.pop("UEFN_DUCKY_PANEL_PORT", None)
    if override is not None:
        env["UEFN_DUCKY_PANEL_PORT"] = override
    # Only import/read configuration. Absolutely no connection to the default port.
    result = subprocess.run([sys.executable, "-c",
        "from frontend.settings import PANEL_LISTENER_PORT, PanelSettings; "
        "print(PANEL_LISTENER_PORT, PanelSettings().port)"],
        env=env, capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == f"{expected} {expected}"


@pytest.mark.parametrize("repo", ["app", "openai", "anthropic", "cursor"])
def test_bootstrap_overrides_inherited_live_address_before_import(repo):
    root = ROOT if repo == "app" else ROOT.parent / "uefn-plugins" / f"uefn-plugin-{repo}"
    bootstrap = root / "conftest.py"
    assert bootstrap.is_file(), bootstrap
    env = dict(os.environ, UEFN_DUCKY_PANEL_PORT="4200", PYTHONPATH=str(ROOT / "ducky_app"))
    result = subprocess.run([sys.executable, "-c",
        "import os, runpy, sys; runpy.run_path(sys.argv[1]); "
        "from frontend.settings import PANEL_LISTENER_PORT; "
        "from backend.bridge.client import DEFAULT_PORT; "
        "assert DEFAULT_PORT == PANEL_LISTENER_PORT - 1; "
        "assert PANEL_LISTENER_PORT == int(os.environ['UEFN_DUCKY_PANEL_PORT']); "
        "assert PANEL_LISTENER_PORT not in (4200, 4201); print('isolated')", str(bootstrap)],
        env=env, capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == "isolated"


# Install the interception BEFORE importing pytest/app code. If the regression
# returns, a would-be live connection is redirected to our ephemeral fake panel,
# never sent to 4199/4200. The fake's request log then makes this proof fail.
_AUTOMATION_PROOF = r'''
import os, socket, sys, traceback
fake_port = int(sys.argv[1])
connect = socket.socket.connect
connect_ex = socket.socket.connect_ex
leaks = []
def safe_address(address):
    if isinstance(address, tuple) and address[0] in ('127.0.0.1', 'localhost', '::1'):
        if address[1] in (4199, 4200, fake_port):
            leaks.append((address, traceback.format_stack(limit=30)))
            return ('127.0.0.1', fake_port)
    return address
socket.socket.connect = lambda self, address: connect(self, safe_address(address))
socket.socket.connect_ex = lambda self, address: connect_ex(self, safe_address(address))
import pytest
code = pytest.main(sys.argv[2:])
from frontend.settings import PANEL_LISTENER_PORT
assert PANEL_LISTENER_PORT not in (4200, 4201, fake_port + 1)
assert not leaks, leaks
raise SystemExit(code)
'''


def test_automation_session_sends_zero_requests_to_fake_owner():
    requests = []

    class OwnerStandIn(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, b''))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def do_POST(self):
            requests.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), OwnerStandIn) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            assert port not in (4199, 4200)
            # Pretend the launching app lives at our fake listener. The child
            # bootstrap must discard that inherited address as well as 4200.
            env = dict(os.environ, UEFN_DUCKY_PANEL_PORT=str(port + 1))
            result = subprocess.run([sys.executable, "-c", _AUTOMATION_PROOF, str(port),
                "ducky_app/backend/automations/test_automations.py",
                "ducky_app/backend/automations/test_workflow_functions.py",
                "ducky_app/backend/automations/test_workflow_groups.py",
                "ducky_app/backend/automations/test_workflow_dataflow.py", "-q"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert requests == [], requests
    print(result.stdout)
    print("Fake owner listener: zero requests; live port redirects: zero")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
@pytest.mark.parametrize("port", [4199, 4200])
def test_a_test_cannot_connect_to_the_running_ducky_or_uefn(host, port):
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="never connect to the running Ducky"):
            sock.connect((host, port))
        assert sock.connect_ex((host, port)) != 0


def test_other_local_ports_still_connect():
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
