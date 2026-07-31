"""Serve the built React panel over HTTP (``file://`` + ES modules is unreliable in WebView2)."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from frontend.settings import PANEL_LISTENER_PORT

PANEL_UI_HTTP_PORT = PANEL_LISTENER_PORT - 1

# Max seconds one /__panel_rpc leg blocks before replying {pending} so the
# caller re-polls. Kept under the client's per-round timeout in backend.panel.rpc.
_RPC_HANDLER_WAIT_S = 20.0

_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()
_root: Path | None = None
_event_cv = threading.Condition()
_event_seq = 0
_event_backlog: deque[tuple[int, dict[str, object]]] = deque(maxlen=4000)
_CUSTOM_DUCKY_RE = re.compile(r"^duckies/custom/([a-z0-9][a-z0-9_-]{0,63})\.png$", re.IGNORECASE)
_TOOL_CAPTURE_RE = re.compile(r"^tool-captures/([A-Za-z0-9._-]+\.(?:png|jpe?g|webp))$", re.IGNORECASE)


def panel_ui_http_url() -> str:
    return f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/"


def publish_panel_events(events: list[dict[str, object]]) -> None:
    """Broadcast agent events over loopback HTTP, avoiding WebView2 evaluate_js.

    Each panel/focus window long-polls with its own cursor, so background-agent
    streaming never competes with pywebview's JS-API completion callbacks.
    """
    global _event_seq
    if not events:
        return
    with _event_cv:
        for event in events:
            _event_seq += 1
            _event_backlog.append((_event_seq, dict(event)))
        _event_cv.notify_all()


def _poll_panel_events(since: int, timeout: float = 20.0) -> tuple[int, list[dict[str, object]]]:
    deadline = time.monotonic() + max(0.0, timeout)
    with _event_cv:
        while _event_seq <= since:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _event_seq, []
            _event_cv.wait(remaining)
        rows = [(seq, event) for seq, event in _event_backlog if seq > since][0:500]
        if not rows:
            return _event_seq, []
        return rows[-1][0], [event for _, event in rows]


def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse a single-range ``Range: bytes=...`` header, clamped to ``file_size``.

    Returns an inclusive ``(start, end)`` byte range, or ``None`` if there is no
    (usable) Range header — callers should then serve the full body. Raises
    ``ValueError`` for a syntactically valid but unsatisfiable range (416).
    """
    if not range_header or file_size <= 0:
        return None
    if not range_header.startswith("bytes="):
        return None
    spec = range_header[len("bytes=") :].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_str, _, end_str = spec.partition("-")
    if start_str == "":
        # Suffix range: "bytes=-500" → last 500 bytes.
        if end_str == "":
            return None
        try:
            suffix_len = int(end_str)
        except ValueError:
            return None
        if suffix_len <= 0:
            raise ValueError("Unsatisfiable range")
        start = max(0, file_size - suffix_len)
        end = file_size - 1
    else:
        try:
            start = int(start_str)
        except ValueError:
            return None
        if end_str == "":
            end = file_size - 1
        else:
            try:
                end = int(end_str)
            except ValueError:
                return None
    if start < 0 or start >= file_size or end < start:
        raise ValueError("Unsatisfiable range")
    end = min(end, file_size - 1)
    return start, end


def verify_panel_dist(dist_root: Path) -> None:
    """Raise if ``index.html`` references bundled assets that are missing on disk."""
    index = dist_root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"Panel dist missing: {index}")
    text = index.read_text(encoding="utf-8")
    for match in re.finditer(r"""(?:src|href)=["'](\./[^"']+)["']""", text):
        rel = match.group(1).removeprefix("./")
        asset = dist_root / rel
        if not asset.is_file():
            raise FileNotFoundError(
                f"Panel build incomplete: index.html references {rel} but file is missing at {asset}. "
                "Run: cd ducky_app/frontend/ui_web/web && npm run build"
            )


def start_panel_ui_server(dist_root: Path) -> str:
    """Start (or reuse) a loopback static server for ``dist_root``; return the panel URL."""
    global _server, _root

    root = dist_root.resolve()
    verify_panel_dist(root)

    with _server_lock:
        if _server is not None and _root == root:
            return panel_ui_http_url()

        if _server is not None:
            try:
                _server.shutdown()
            except Exception:
                pass
            _server = None

        _root = root

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def handle(self) -> None:
                host = self.client_address[0]
                if host not in ("127.0.0.1", "::1"):
                    self.send_error(403)
                    return
                super().handle()

            def _read_json_body(self) -> object:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                body = self.rfile.read(length) if length > 0 else b""
                return json.loads(body.decode("utf-8")) if body else None

            def _send_json(self, status: int, obj: object) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                # Block sandboxed plugin iframes (Origin: null) and foreign origins
                # from hitting panel control endpoints. Same-origin / no-Origin OK.
                from backend.uefn_plugins.webview import panel_post_origin_allowed

                origin = self.headers.get("Origin")
                if not panel_post_origin_allowed(origin, panel_ui_http_url()):
                    self.send_error(403)
                    return
                # Cross-process event bridge: the stdio MCP bridge runs in a
                # SEPARATE process from this panel, so its notify_chats_changed /
                # agent-stream pushes can't reach the UI directly. It POSTs them
                # here (loopback-only, gated in handle()) and we replay them onto
                # the panel's own push pipeline so spawned chats appear + stream.
                if path == "/__panel_event":
                    try:
                        payload = self._read_json_body()
                    except Exception:
                        self.send_error(400)
                        return
                    events = payload if isinstance(payload, list) else [payload]
                    try:
                        from frontend.ui_web.agent_modes import get_panel_push

                        push = get_panel_push()
                        if push is not None:
                            for ev in events:
                                if isinstance(ev, dict):
                                    push(ev)
                    except Exception:
                        pass
                    self.send_response(204)
                    self.end_headers()
                    return

                # Run delegation: the bridge asks THIS process to actually run a
                # chat's agent so the spawned duck is a normal panel session
                # (running dot, native streaming, reconcile). _local=True stops
                # run_message from delegating back to us (no loop).
                if path == "/__panel_run":
                    try:
                        payload = self._read_json_body()
                    except Exception:
                        self.send_error(400)
                        return
                    if not isinstance(payload, dict):
                        self.send_error(400)
                        return
                    conv_id = str(payload.get("conv_id") or "").strip()
                    text = str(payload.get("text") or "")
                    mode = str(payload.get("mode") or "agent")
                    model = str(payload.get("model") or "")
                    attachments = payload.get("attachments") or None
                    if not conv_id or (not text.strip() and not attachments):
                        self._send_json(
                            400,
                            {"status": "error", "error": "conv_id and text or attachments required"},
                        )
                        return
                    try:
                        from frontend.ui_web.agent_modes import run_message, run_message_and_wait

                        if payload.get("wait"):
                            outcome = run_message_and_wait(
                                conv_id,
                                text,
                                mode,
                                model,
                                timeout_sec=(
                                    180.0
                                    if payload.get("timeout_sec") is None
                                    else float(payload.get("timeout_sec"))
                                ),
                                cancel_on_timeout=bool(payload.get("cancel_on_timeout", True)),
                                parent=str(payload.get("parent_conv_id") or ""),
                                _local=True,
                            )
                            self._send_json(200, outcome)
                        else:
                            run_id = run_message(
                                conv_id,
                                text,
                                mode,
                                model,
                                attachments=attachments,
                                force=bool(payload.get("force")),
                                parent=str(payload.get("parent_conv_id") or ""),
                                _local=True,
                            )
                            self._send_json(200, {"status": "running", "conv_id": conv_id, "run_id": run_id})
                    except Exception as exc:
                        self._send_json(500, {"status": "error", "error": str(exc)})
                    return

                # Second-instance / Open-with handoff: another UEFN-Ducky.exe was
                # launched with file paths (or just to focus). Deliver to this panel.
                if path == "/__panel_open_files":
                    try:
                        payload = self._read_json_body()
                    except Exception:
                        self.send_error(400)
                        return
                    paths: list[str] = []
                    links: list[str] = []
                    if isinstance(payload, dict):
                        raw = payload.get("paths") or []
                        if isinstance(raw, list):
                            paths = [str(p) for p in raw if str(p).strip()]
                        raw_links = payload.get("links") or []
                        if isinstance(raw_links, list):
                            links = [str(link) for link in raw_links if str(link).strip()]
                    from frontend.open_files import dispatch_deep_links, dispatch_open_files

                    ok_links = dispatch_deep_links(links) if links else False
                    # Focus-only handoff (no paths, no links) still goes through
                    # dispatch_open_files so the window is shown.
                    ok_paths = dispatch_open_files(paths) if (paths or not links) else True
                    ok = ok_paths or ok_links
                    self._send_json(
                        200 if ok else 503,
                        {"ok": ok, "count": len(paths) + len(links)},
                    )
                    return

                # UI request/response: a tool (bridge or embedded agent) asks the
                # panel to navigate / list targets / spotlight and wait for an
                # answer. We push a ui_rpc_request event to React and block up to
                # _RPC_HANDLER_WAIT_S for its reply; if the user hasn't answered
                # yet (e.g. require_click), reply {pending} so the caller re-polls.
                if path == "/__panel_rpc":
                    try:
                        payload = self._read_json_body()
                    except Exception:
                        self.send_error(400)
                        return
                    if not isinstance(payload, dict):
                        self.send_error(400)
                        return
                    method = str(payload.get("method") or "").strip()
                    params = payload.get("params")
                    if not method or not isinstance(params, dict):
                        self._send_json(400, {"error": "method and params required"})
                        return
                    from frontend.ui_web import ui_rpc
                    from frontend.ui_web.agent_modes import get_panel_push

                    push = get_panel_push()
                    if push is None:
                        # No React panel in this process to answer the request.
                        self._send_json(200, {"result": {"error": "panel not open"}})
                        return
                    request_id, event = ui_rpc.submit(method, params)
                    push(event)
                    result = ui_rpc.wait(request_id, _RPC_HANDLER_WAIT_S)
                    if result is None:
                        self._send_json(200, {"pending": True, "request_id": request_id})
                    else:
                        self._send_json(200, {"result": result})
                    return

                self.send_error(404)

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/__panel_events":
                    query = parse_qs(parsed.query)
                    try:
                        since = max(0, int((query.get("since") or ["0"])[0]))
                    except (TypeError, ValueError):
                        since = 0
                    cursor, events = _poll_panel_events(since)
                    self._send_json(200, {"cursor": cursor, "events": events})
                    return
                # Re-poll leg of a long UI request (require_click): the POST leg
                # returned {pending, request_id} and the caller waits here until
                # the user answers or its own budget runs out.
                if parsed.path == "/__panel_rpc":
                    query = parse_qs(parsed.query)
                    request_id = (query.get("id") or [""])[0].strip()
                    if not request_id:
                        self._send_json(400, {"error": "id required"})
                        return
                    from frontend.ui_web import ui_rpc

                    result = ui_rpc.wait(request_id, _RPC_HANDLER_WAIT_S)
                    if result is None:
                        self._send_json(200, {"pending": True, "request_id": request_id})
                    else:
                        self._send_json(200, {"result": result})
                    return
                # Keep the raw (still-percent-encoded) path for routes whose first
                # segment intentionally embeds %2F (model-files dir tokens).
                raw_rel = parsed.path.lstrip("/") or "index.html"
                req_path = unquote(parsed.path)
                rel = "index.html" if req_path in ("", "/") else req_path.lstrip("/")

                custom_match = _CUSTOM_DUCKY_RE.match(rel)
                if custom_match:
                    from frontend.ducky_assets import custom_ducky_path

                    try:
                        file_path = custom_ducky_path(custom_match.group(1).lower())
                    except ValueError:
                        self.send_error(404)
                        return
                    if not file_path.is_file():
                        self.send_error(404)
                        return
                    data = file_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                capture_match = _TOOL_CAPTURE_RE.match(rel)
                if capture_match:
                    from frontend.ui_web.tool_captures import resolve_tool_capture_path
                    from frontend.ui_web.project_media import media_content_type

                    try:
                        file_path = resolve_tool_capture_path(capture_match.group(1))
                    except ValueError:
                        self.send_error(404)
                        return
                    try:
                        data = file_path.read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", media_content_type(file_path))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "private, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                from frontend.ui_web.project_media import (
                    media_content_type,
                    model_files_re,
                    project_media_re,
                    resolve_model_files_path,
                    resolve_project_media_path,
                )

                media_match = project_media_re().match(rel)
                if media_match:
                    try:
                        file_path = resolve_project_media_path(media_match.group(1))
                    except ValueError:
                        self.send_error(404)
                        return
                    try:
                        file_size = file_path.stat().st_size
                    except OSError:
                        self.send_error(404)
                        return
                    try:
                        byte_range = _parse_range_header(self.headers.get("Range"), file_size)
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.end_headers()
                        return
                    mime = media_content_type(file_path)
                    try:
                        with file_path.open("rb") as fh:
                            if byte_range is not None:
                                start, end = byte_range
                                length = end - start + 1
                                fh.seek(start)
                                data = fh.read(length)
                                self.send_response(206)
                                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                            else:
                                data = fh.read()
                                self.send_response(200)
                    except OSError:
                        self.send_error(404)
                        return
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # Match on raw_rel so quote(..., safe="") dir tokens keep %2F as one segment.
                # Unquoting first turns %2F into "/" and breaks ext:/abs: nested paths → 404.
                model_match = model_files_re().match(raw_rel)
                if model_match:
                    try:
                        file_path = resolve_model_files_path(
                            model_match.group(1), model_match.group(2)
                        )
                    except ValueError:
                        self.send_error(404)
                        return
                    try:
                        data = file_path.read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", media_content_type(file_path))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # Plugin webview assets (Phase 2) — see backend.uefn_plugins.webview.
                from backend.uefn_plugins.webview import try_serve_plugin_ui, try_serve_user_sound

                if try_serve_plugin_ui(self, rel):
                    return
                if try_serve_user_sound(self, rel):
                    return

                file_path = (root / rel).resolve()
                try:
                    file_path.relative_to(root)
                except ValueError:
                    self.send_error(403)
                    return
                if not file_path.is_file():
                    self.send_error(404)
                    return
                mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        _server = ThreadingHTTPServer(("127.0.0.1", PANEL_UI_HTTP_PORT), Handler)
        threading.Thread(target=_server.serve_forever, daemon=True, name="panel-ui-http").start()

    return panel_ui_http_url()
