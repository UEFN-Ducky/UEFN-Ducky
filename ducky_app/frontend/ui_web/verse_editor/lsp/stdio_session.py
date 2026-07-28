"""LSP stdio session for ephemeral verse-lsp scans (not the UI WebSocket bridge)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote

from frontend.ui_web.verse_editor.lsp.bridge import (
    _lsp_read_message,
    _lsp_write_message,
    _spawn_lsp_hidden,
)
from frontend.ui_web.verse_editor.lsp.detect import detect_verse_lsp


def to_monaco_file_uri(abs_path: str) -> str:
    full = abs_path.replace("\\", "/")
    if len(full) >= 2 and full[1] == ":":
        drive = full[0].lower()
        rest = full[2:]
        path_part = f"{drive}:{rest}"
        return "file:///" + quote(path_part, safe="/")
    return "file:///" + quote(full.lstrip("/"), safe="/")


class LspStdioSession:
    """Drive verse-lsp over Content-Length stdio framing."""

    def __init__(self, project_root: str) -> None:
        detect = detect_verse_lsp()
        if not detect.get("available"):
            raise RuntimeError(str(detect.get("error") or "verse-lsp not found"))
        self.lsp_path = Path(str(detect["path"]))
        self.project_root = project_root
        self._next_id = 1
        self._proc: subprocess.Popen[bytes] | None = None
        self._inbound: list[dict[str, Any]] = []
        self._pending: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._diag_by_uri: dict[str, list[dict[str, Any]]] = {}

    def file_uri(self, relative_path: str = "") -> str:
        root = self.project_root.replace("\\", "/").rstrip("/")
        rel = relative_path.replace("\\", "/").lstrip("/")
        abs_path = f"{root}/{rel}" if rel else root
        return to_monaco_file_uri(abs_path)

    def start(self) -> None:
        self._proc = _spawn_lsp_hidden(self.lsp_path, self.project_root)
        assert self._proc.stdout is not None
        assert self._proc.stdin is not None
        if self._proc.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_alive(self) -> bool:
        """True while the verse-lsp process is still running (poll() is None).

        A crashed/killed server that stops answering pulls must NOT be mistaken for
        "analyzed, clean" — the scan uses this to refuse a false all-clear.
        """
        proc = self._proc
        return proc is not None and proc.poll() is None

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_s: float = 30.0,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                for msg in reversed(self._inbound):
                    if predicate(msg):
                        return msg
            time.sleep(0.05)
        return None

    def request(self, method: str, params: object, timeout_s: float = 30.0) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                pending = self._pending.pop(msg_id, None)
            if pending is not None:
                if "error" in pending:
                    raise RuntimeError(str(pending["error"]))
                return pending.get("result")
            time.sleep(0.02)
        raise TimeoutError(f"LSP request timed out: {method}")

    def notify(self, method: str, params: object) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def diagnostics_for_uri(self, uri: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._diag_by_uri.get(uri, []))

    @staticmethod
    def _norm_uri(uri: str) -> str:
        return unquote(uri).replace("\\", "/").lower()

    def published_diagnostics(self, uri: str) -> list[dict[str, Any]] | None:
        """Diagnostics the server PUSHED for uri, or None when it never published for it.

        An empty list means "analyzed, clean" — callers must not confuse that with
        None ("no report yet"). Falls back to a normalized comparison because the
        server may echo uris with different casing/percent-encoding than ours.
        """
        with self._lock:
            if uri in self._diag_by_uri:
                return list(self._diag_by_uri[uri])
            want = self._norm_uri(uri)
            for key, diags in self._diag_by_uri.items():
                if self._norm_uri(key) == want:
                    return list(diags)
        return None

    def diagnostic_uri_count(self) -> int:
        with self._lock:
            return len(self._diag_by_uri)

    def handle_server_requests(self, msg: dict[str, Any]) -> None:
        if "method" not in msg or "id" not in msg:
            return
        method = str(msg["method"])
        params = msg.get("params")
        if method in ("client/registerCapability", "client/unregisterCapability"):
            result = None
        elif method == "workspace/configuration":
            items = (params or {}).get("items") if isinstance(params, dict) else None
            result = [({}) for _ in items] if isinstance(items, list) else []
        elif method == "window/workDoneProgress/create":
            result = None
        elif method == "workspace/applyEdit":
            result = {"applied": False}
        else:
            result = None
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("LSP process not running")
        _lsp_write_message(proc.stdin, json.dumps(payload))

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while not self._stop.is_set():
                line = proc.stderr.readline()
                if not line:
                    break
        except OSError:
            pass

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while not self._stop.is_set():
            raw = _lsp_read_message(proc.stdout)
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                self._dispatch(msg)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if msg.get("method") and msg.get("id") is not None and "result" not in msg and "error" not in msg:
            self.handle_server_requests(msg)
        method = msg.get("method")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            if isinstance(params, dict):
                uri = str(params.get("uri") or "")
                diags = params.get("diagnostics")
                if uri and isinstance(diags, list):
                    with self._lock:
                        self._diag_by_uri[uri] = diags
        with self._lock:
            self._inbound.append(msg)
            msg_id = msg.get("id")
            if msg_id is not None and ("result" in msg or "error" in msg):
                self._pending[int(msg_id)] = msg
