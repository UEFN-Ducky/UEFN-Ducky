"""TCP client for UEFN Verse Workflow Server (127.0.0.1:1962)."""

from __future__ import annotations

import socket
import threading
from typing import Any, Callable

from frontend.ui_web.verse_editor.workflow.protocol import BuildState
from frontend.ui_web.verse_editor.workflow.protocol_client import VerseWorkflowProtocolClient

DEFAULT_PORT = 1962
DEFAULT_ADDRESS = "127.0.0.1"


class VerseWorkflowClient(VerseWorkflowProtocolClient):
    def __init__(self) -> None:
        super().__init__()
        self.connected = False
        self.build_state = int(BuildState.NoBuild)
        self.can_push_verse_changes = False
        self.last_log = ""
        self._on_state: Callable[[dict[str, Any]], None] | None = None
        self._create_handlers()

    def set_state_listener(self, listener: Callable[[dict[str, Any]], None] | None) -> None:
        self._on_state = listener

    def _notify_state(self) -> None:
        if self._on_state:
            self._on_state(self.get_status())

    def _create_handlers(self) -> None:
        def on_log(params: dict[str, Any]) -> None:
            self.last_log = str(params.get("message") or "")

        def on_build(state: int) -> None:
            self.build_state = int(state)
            self._notify_state()

        def on_can_push(value: bool) -> None:
            self.can_push_verse_changes = bool(value)
            self._notify_state()

        self.on("logMessage", on_log)
        self.on("updateBuildState", on_build)
        self.on("canPushVerseChanges", on_can_push)

    def start(
        self,
        port: int = DEFAULT_PORT,
        address: str = DEFAULT_ADDRESS,
        timeout_s: float = 5.0,
    ) -> None:
        if self.connected:
            return
        sock = socket.create_connection((address, port), timeout=timeout_s)
        sock.settimeout(None)
        self.connect_socket(sock)
        self.connected = True
        self._notify_state()

    def stop(self) -> None:
        self.connected = False
        self.disconnect()
        self._notify_state()

    def compile_project(self) -> dict[str, Any]:
        result = self.send_request("compileProject", {})
        if not isinstance(result, dict):
            raise RuntimeError("Unexpected compileProject result")
        return result

    def push_changes(self, verse_only: bool = True) -> str:
        result = self.send_request("pushChanges", verse_only)
        return str(result)

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "build_state": self.build_state,
            "can_push_verse_changes": self.can_push_verse_changes,
            "last_log": self.last_log,
        }


_workflow: VerseWorkflowClient | None = None
_workflow_lock = threading.Lock()


def get_workflow_client() -> VerseWorkflowClient:
    global _workflow
    with _workflow_lock:
        if _workflow is None:
            _workflow = VerseWorkflowClient()
        return _workflow
