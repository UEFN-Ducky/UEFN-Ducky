"""The editor's verse-lsp is stopped once its client is gone and nobody reattached.

Nov 16 2026: one verse-lsp.exe held 1,458 MB at 96% of a core. When the editor's WebSocket
client dropped, only the listener was torn down; the process lived on, connected to no one.
"""

from __future__ import annotations

import socket
import threading
import time

from frontend.ui_web.verse_editor.lsp import bridge
from frontend.ui_web.verse_editor.lsp.bridge import LspBridge


class _Proc:
    stdin = None

    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_an_unattached_server_is_stopped_and_an_attached_or_replaced_one_is_left() -> None:
    b, proc = LspBridge(), _Proc()
    b._proc = proc
    b._server_sock = object()  # a listener came back (the editor reloaded)
    b._stop_if_unattached(proc)
    assert not proc.terminated
    b._server_sock = None
    b._stop_if_unattached(_Proc())  # a timer for a process that was replaced since
    assert not proc.terminated and b._proc is proc
    b._stop_if_unattached(proc)
    assert proc.terminated and b._proc is None


def test_a_client_that_leaves_starts_the_idle_stop(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_IDLE_STOP_S", 0.2)
    b, proc = LspBridge(), _Proc()
    b._proc = proc
    b._spawn_bridge_listener(proc)
    client = socket.create_connection(("127.0.0.1", b._port), timeout=5)
    client.sendall(b"GET / HTTP/1.1\r\nSec-WebSocket-Key: abc\r\n\r\n")
    assert client.recv(4096).startswith(b"HTTP/1.1 101")
    client.close()  # the editor tab closed; proc.stdin is None, so the loop ends here too
    deadline = time.monotonic() + 5
    while not proc.terminated and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.terminated
    assert not [t for t in threading.enumerate() if t.name == "verse-lsp-bridge" and t.is_alive()]
