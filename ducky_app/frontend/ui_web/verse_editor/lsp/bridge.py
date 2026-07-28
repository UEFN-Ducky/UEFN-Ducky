"""stdio verse-lsp subprocess bridged to a localhost WebSocket for Monaco."""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from frontend.ui_web.verse_editor.lsp.detect import detect_verse_lsp
from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_STDERR_RING_MAX = 80
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _spawn_lsp_hidden(lsp_path: Path, cwd: str) -> subprocess.Popen[bytes]:
    """Start verse-lsp without a visible console window on Windows."""
    kwargs: dict[str, object] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "bufsize": 0,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return subprocess.Popen([str(lsp_path)], **kwargs)  # type: ignore[arg-type]


def _ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def _read_exact_stream(stream, n: int) -> bytes:
    """Read exactly n bytes from a pipe/file stream (bufsize=0 may return partial reads)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _parse_ws_frame(sock: socket.socket) -> bytes:
    header = _read_exact(sock, 2)
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4) if masked else b""
    payload = _read_exact(sock, length)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return payload


def _send_ws_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    frame = bytearray([0x81])
    ln = len(data)
    if ln < 126:
        frame.append(ln)
    elif ln < 65536:
        frame.append(126)
        frame.extend(struct.pack("!H", ln))
    else:
        frame.append(127)
        frame.extend(struct.pack("!Q", ln))
    frame.extend(data)
    sock.sendall(frame)


def _lsp_read_message(stream) -> str | None:
    """Read LSP Content-Length framed message from stdout."""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            break
        if ":" in decoded:
            k, v = decoded.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = _read_exact_stream(stream, length)
    if len(body) < length:
        return None
    return body.decode("utf-8", errors="replace")


def _lsp_write_message(stream, text: str) -> None:
    data = text.encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
    stream.write(header + data)
    stream.flush()


class LspBridge:
    """Manage verse-lsp subprocess and WebSocket bridge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Serializes start()/stop(): concurrent callers (editor bind, project scan,
        # reconnect retries) must not stop() each other's freshly spawned verse-lsp.
        self._start_lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._watcher_thread: threading.Thread | None = None
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._port: int = 0
        self._project_root: str = ""
        self._error: str = ""
        self._last_exit_code: int | None = None
        self._stop = threading.Event()
        self._stderr_ring: deque[str] = deque(maxlen=_STDERR_RING_MAX)
        self._watched_verse_mtimes: dict[str, float] = {}
        self._pending_fs_events: deque[dict[str, object]] = deque(maxlen=200)
        self._fs_event_debounce_s = 2.0
        self._fs_poll_interval_s = 3.0
        self._last_fs_notify_by_uri: dict[str, float] = {}
        self._stdin_lock = threading.Lock()
        self._ws_send_lock = threading.Lock()
        self._bridge_listening = threading.Event()

    def _proc_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _bridge_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and self._bridge_listening.is_set()

    def get_status(self) -> dict[str, Any]:
        detect = detect_verse_lsp()
        with self._lock:
            running = self._proc_running()
            bridge_up = self._bridge_alive()
            ws_url = (
                f"ws://127.0.0.1:{self._port}"
                if running and self._port and bridge_up
                else ""
            )
            return {
                "available": bool(detect.get("available")),
                "lsp_path": str(detect.get("path") or ""),
                "source": str(detect.get("source") or ""),
                "running": running and bridge_up,
                "ws_url": ws_url,
                "project_root": self._project_root,
                "error": self._error or str(detect.get("error") or ""),
                "stderr_tail": list(self._stderr_ring),
                # Diagnostics: distinguish "process crashed" from "listener died but proc alive".
                "proc_alive": running,
                "bridge_up": bridge_up,
                "last_exit_code": self._last_exit_code,
            }

    def start(self, project_root: str) -> dict[str, Any]:
        with self._start_lock:
            return self._start_locked(project_root)

    def _start_locked(self, project_root: str) -> dict[str, Any]:
        root = normalize_verse_lsp_project_root(project_root)
        if not root:
            self._error = "No project root set"
            return self.get_status()

        with self._lock:
            same_root = self._project_root == root
            if self._proc_running() and same_root and self._bridge_alive():
                self._error = ""
                return self.get_status()

        # Process alive but the listener died (previous WebSocket client dropped): reattach a
        # fresh listener to the existing process instead of restarting verse-lsp. A second
        # `initialize` from the new client just gets an error response per the LSP spec — it
        # does not crash the server — and this avoids the process/listener churn that broke the
        # boot handshake when we restarted on every reconnect.
        if self._proc_running() and self._project_root == root:
            self._stop_bridge_listener()
            proc = self._proc
            if proc is not None:
                self._spawn_bridge_listener(proc)
                self._error = ""
                return self.get_status()

        self._stop_locked()
        detect = detect_verse_lsp()
        if not detect.get("available"):
            self._error = str(detect.get("error") or "verse-lsp not found")
            return self.get_status()

        lsp_path = Path(str(detect["path"]))
        self._project_root = root
        self._stop.clear()
        self._last_exit_code = None
        self._stderr_ring.clear()
        self._pending_fs_events.clear()
        self._watched_verse_mtimes.clear()

        try:
            self._proc = _spawn_lsp_hidden(lsp_path, root)
        except OSError as exc:
            self._error = str(exc)
            return self.get_status()

        proc = self._proc
        if proc.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(proc.stderr,),
                daemon=True,
                name="verse-lsp-stderr",
            )
            self._stderr_thread.start()

        # ONE stdout reader for the lifetime of the process. Per-connection readers
        # left a stale thread blocked in readline() after reattach — two readers on
        # one pipe, with the old one stealing the new client's initialize response.
        threading.Thread(
            target=self._pump_stdout,
            args=(proc,),
            daemon=True,
            name="verse-lsp-stdout-pump",
        ).start()

        # NOTE: the 3s mtime poller over Content/**/*.verse is intentionally NOT started.
        # It fired didChangeWatchedFiles for every autosave (1.5s cadence while typing)
        # and for UEFN's own file touches, making verse-lsp re-analyze from disk on top
        # of the didChange it already processed — the server stayed permanently busy and
        # every interactive request timed out. VS Code has no such poller: open files
        # sync via didChange; .vproject/.vpackage changes still arrive via the client's
        # one-shot didChangeWatchedFiles (notifyWatchFiles).

        self._spawn_bridge_listener(proc)
        threading.Thread(
            target=self._watch_proc_exit,
            args=(proc,),
            daemon=True,
            name="verse-lsp-proc-watch",
        ).start()
        self._error = ""
        return self.get_status()

    def _spawn_bridge_listener(self, proc: subprocess.Popen[bytes]) -> None:
        self._bridge_listening.clear()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self._port = server.getsockname()[1]
        self._server_sock = server
        self._thread = threading.Thread(
            target=self._bridge_loop,
            args=(server, proc),
            daemon=True,
            name="verse-lsp-bridge",
        )
        self._thread.start()
        if not self._bridge_listening.wait(timeout=5.0):
            self._error = "verse-lsp bridge failed to start listening"
            self._port = 0

    def _stop_bridge_listener(self) -> None:
        self._bridge_listening.clear()
        if self._client_sock:
            try:
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        self._port = 0

    def stop(self) -> None:
        with self._start_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop.set()
        self._stop_bridge_listener()
        with self._lock:
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
        self._stop.clear()

    def _watch_proc_exit(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            code = proc.wait()
        except Exception:
            return
        with self._lock:
            if self._proc is not proc:
                # Stale watcher for a process replaced by a newer start(): touching the
                # listener or state here tore down the NEW session (WinError 10038 on
                # its accept loop). This process's death is old news — do nothing.
                return
            self._last_exit_code = code
            self._proc = None
            self._port = 0
            self._error = f"verse-lsp exited (code {code}) — reopen a .verse file to reconnect"
        self._stop_bridge_listener()

    def _pump_stdout(self, proc: subprocess.Popen[bytes]) -> None:
        """Forward every LSP stdout message of this process to the current WS client.

        Runs for the process lifetime. Messages arriving while no client is attached
        (or mid-reattach) are dropped — verse-lsp re-publishes diagnostics on the next
        didChange/pull, so nothing user-visible is lost.
        """
        stdout = proc.stdout
        if stdout is None:
            return
        while True:
            msg = _lsp_read_message(stdout)
            if msg is None:
                return
            if self._proc is not proc:
                return
            client = self._client_sock
            if client is None:
                continue
            try:
                with self._ws_send_lock:
                    _send_ws_text(client, msg)
            except OSError:
                continue

    def _append_stderr(self, line: str) -> None:
        text = line.rstrip()
        if text:
            with self._lock:
                self._stderr_ring.append(text)

    def _drain_stderr(self, stream) -> None:
        try:
            while not self._stop.is_set():
                line = stream.readline()
                if not line:
                    break
                self._append_stderr(line.decode("utf-8", errors="replace"))
        except OSError:
            pass

    def _content_verse_root(self) -> Path | None:
        root = Path(self._project_root)
        content = root / "Content"
        if content.is_dir():
            return content
        if root.name.lower() == "content" and root.is_dir():
            return root
        return None

    def _watch_verse_files(self) -> None:
        while not self._stop.is_set():
            content = self._content_verse_root()
            if content is not None:
                try:
                    now = time.monotonic()
                    for path in content.rglob("*.verse"):
                        if not path.is_file():
                            continue
                        key = str(path.resolve())
                        mtime = path.stat().st_mtime
                        prev = self._watched_verse_mtimes.get(key)
                        if prev is not None and mtime != prev:
                            uri = path.as_uri()
                            last_notify = self._last_fs_notify_by_uri.get(uri, 0.0)
                            if now - last_notify >= self._fs_event_debounce_s:
                                self._pending_fs_events.append({"uri": uri, "type": 2})
                                self._last_fs_notify_by_uri[uri] = now
                        self._watched_verse_mtimes[key] = mtime
                except OSError:
                    pass
            time.sleep(self._fs_poll_interval_s)

    def _flush_pending_fs_events(self, stdin) -> None:
        events: list[dict[str, object]] = []
        while self._pending_fs_events:
            events.append(self._pending_fs_events.popleft())
        if not events:
            return
        import json

        msg = {
            "jsonrpc": "2.0",
            "method": "workspace/didChangeWatchedFiles",
            "params": {"changes": events},
        }
        payload = json.dumps(msg)
        with self._stdin_lock:
            try:
                _lsp_write_message(stdin, payload)
            except OSError:
                pass

    def _bridge_loop(self, server: socket.socket, proc: subprocess.Popen[bytes]) -> None:
        """Accept ONE WebSocket client and forward its frames to verse-lsp stdin.

        stdout→client forwarding lives in the per-process _pump_stdout thread.
        All shared-state mutations are identity-guarded: a stale loop (its listener
        replaced by a newer start()) must not clobber the new session's state.
        """
        client: socket.socket | None = None
        try:
            self._bridge_listening.set()
            server.settimeout(0.5)
            while not self._stop.is_set() and self._server_sock is server:
                try:
                    client, _ = server.accept()
                    break
                except socket.timeout:
                    if proc.poll() is not None:
                        if self._proc is proc:
                            self._error = "verse-lsp exited unexpectedly"
                        return
                except OSError:
                    # Listener closed under us (reattach/stop) — not an error state.
                    return
            if client is None:
                return

            req = b""
            while b"\r\n\r\n" not in req:
                chunk = client.recv(4096)
                if not chunk:
                    return
                req += chunk
            headers: dict[str, str] = {}
            for line in req.decode("utf-8", errors="replace").split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            key = headers.get("sec-websocket-key", "")
            accept = _ws_accept_key(key)
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            client.sendall(resp.encode())

            stdin = proc.stdin
            if stdin is None:
                return

            # Attach only after the handshake so _pump_stdout can never interleave an
            # LSP frame into the middle of the HTTP 101 response.
            self._client_sock = client

            while not self._stop.is_set():
                try:
                    payload = _parse_ws_frame(client)
                except (ConnectionError, OSError):
                    break
                if not payload:
                    continue
                try:
                    with self._stdin_lock:
                        _lsp_write_message(stdin, payload.decode("utf-8"))
                        self._flush_pending_fs_events(stdin)
                except OSError:
                    break
        except Exception as exc:
            if self._server_sock is server:
                self._error = str(exc)
        finally:
            if self._client_sock is client:
                self._client_sock = None
            if client:
                try:
                    client.close()
                except OSError:
                    pass
            try:
                server.close()
            except OSError:
                pass
            # Only tear down shared state if this loop still owns the session; a stale
            # loop exiting must not zero the port of a newly attached listener.
            if self._server_sock is server:
                self._bridge_listening.clear()
                with self._lock:
                    self._port = 0
                self._server_sock = None
