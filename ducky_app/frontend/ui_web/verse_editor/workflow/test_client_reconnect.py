"""A Workflow Server socket UEFN drops marks the client disconnected, so the next compile reconnects."""

from __future__ import annotations

import socket
import time

from frontend.ui_web.verse_editor.workflow.client import VerseWorkflowClient


def _wait(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_server_close_marks_disconnected_and_start_reconnects() -> None:
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    try:
        client = VerseWorkflowClient()
        client.start(port=port, timeout_s=2.0)
        first, _ = server.accept()
        assert client.connected
        first.close()  # UEFN closed or restarted
        assert _wait(lambda: not client.connected)
        client.start(port=port, timeout_s=2.0)  # what workspace_compile_verse does when not connected
        second, _ = server.accept()
        assert client.connected
        client.stop()
        second.close()
    finally:
        server.close()


def test_stop_then_start_is_not_undone_by_the_old_reader() -> None:
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    try:
        client = VerseWorkflowClient()
        client.start(port=port, timeout_s=2.0)
        first, _ = server.accept()
        client.stop()
        client.start(port=port, timeout_s=2.0)
        second, _ = server.accept()
        first.close()
        time.sleep(0.2)  # the old reader exits; it must not touch the new connection
        assert client.connected
        client.stop()
        second.close()
    finally:
        server.close()
