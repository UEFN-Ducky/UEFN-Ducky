"""Plugin webview UI — serve sandboxed HTML from installed plugin packages.

Isolated Phase-2 surface. Existing hosts call into this module with one-liners:

- ``panel_httpd`` → ``try_serve_plugin_ui(handler, rel_path)``
- ``host._load_one`` → ``merge_ui_panels(...)``

Delete this file (+ the one-line hooks) to remove the feature.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

# Route: /plugin-ui/<plugin_id>/<relative/path>
PLUGIN_UI_ROUTE_RE = re.compile(
    r"^plugin-ui/([a-z][a-z0-9_-]{0,63})/(.+)$",
    re.IGNORECASE,
)

# Route: /plugin-ui/_kit/<file>: the Ducky plugin UI kit (ui_kit.py), shared by every plugin.
PLUGIN_UI_KIT_ROUTE_RE = re.compile(r"^plugin-ui/_kit/([a-z0-9._-]+)$", re.IGNORECASE)

# Route: /user-sounds/<filename>
USER_SOUNDS_ROUTE_RE = re.compile(
    r"^user-sounds/([^/\\]+)$",
    re.IGNORECASE,
)
_SOUND_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PANEL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def sanitize_entry(entry: str, plugin_root: Path) -> str | None:
    """Return a jail-safe relative entry path, or None if hostile / missing.

    Rejects absolute paths, ``..`` segments, and paths that escape ``plugin_root``.
    The file must exist under the plugin directory.
    """
    raw = (entry or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("~"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    rel = "/".join(parts)
    try:
        resolved = (plugin_root / rel).resolve()
        resolved.relative_to(plugin_root.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return rel


def merge_ui_panels(
    contributes: dict[str, Any],
    plugin_id: str,
    plugin_root: Path,
    *,
    version: int | str | None = None,
) -> list[dict[str, Any]]:
    """Parse ``contributes.ui.panels`` into contribution rows (empty if none/invalid)."""
    from backend.uefn_plugins.plugin_version import format_plugin_version

    raw = contributes.get("ui.panels") or contributes.get("ui_panels") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        panel_id = str(item.get("id") or "").strip().lower()
        if not _PANEL_ID_RE.match(panel_id):
            continue
        entry = sanitize_entry(str(item.get("entry") or "ui/index.html"), plugin_root)
        if not entry:
            continue
        row: dict[str, Any] = {
            "id": panel_id,
            "title": str(item.get("title") or panel_id),
            "icon": str(item.get("icon") or "duck"),
            "entry": entry,
            "plugin_id": plugin_id,
        }
        if version is not None:
            row["version"] = format_plugin_version(version)
        out.append(row)
    return out


def resolve_plugin_ui_file(plugin_id: str, rel_path: str) -> Path | None:
    """Resolve a path under an enabled plugin's AppData dir, or None."""
    from backend.uefn_plugins.host import is_plugin_enabled
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir, normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return None
    if not _PLUGIN_ID_RE.match(pid):
        return None
    if not is_plugin_enabled(pid):
        return None

    raw = unquote(rel_path or "").replace("\\", "/").lstrip("/")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None

    root = (appdata_uefn_plugins_dir() / pid).resolve()
    if not root.is_dir():
        return None
    try:
        target = (root.joinpath(*parts)).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    return target


# Opaque plugin iframes (Origin null) cannot CORS-post CF RUM. Do not allow
# static.cloudflareinsights.com — that stops the injected beacon from running.
# No outside code: a plugin ships every library it uses inside its package (ui/vendor/),
# so a panel works offline and never runs code that changed after it was published.
PLUGIN_UI_HTML_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' data: blob:; "
    # https media like https images: team data players (short presigned storage links).
    "font-src 'self' data:; media-src 'self' data: blob: https:; "
    "object-src 'none'; base-uri 'self'"
)


def plugin_ui_csp(mime: str) -> str | None:
    """CSP for HTML plugin-ui only — JS/CSS keep working as classic subresources."""
    kind = (mime or "").split(";", 1)[0].strip().lower()
    if kind == "text/html":
        return PLUGIN_UI_HTML_CSP
    return None


def send_plugin_ui_error(handler: Any, code: int) -> None:
    """404/403 for plugin-ui with CORS so opaque-origin fetch() sees the status."""
    handler.send_response(code)
    handler.send_header("Content-Type", "text/plain")
    handler.send_header("Content-Length", "0")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()


_HTML_HEAD_RE = re.compile(rb"<head\b[^>]*>|<html\b[^>]*>|<!doctype\b[^>]*>", re.IGNORECASE)


def with_panel_error_script(html: bytes) -> bytes:
    """``html`` with the panel error reporter (``ui_kit.PANEL_ERROR_SCRIPT``) first:
    right after ``<head>`` (else ``<html>``, else the doctype, so the page never drops
    into quirks mode), before any of the plugin's own scripts run."""
    from backend.uefn_plugins.ui_kit import PANEL_ERROR_SCRIPT

    found: dict[bytes, re.Match[bytes]] = {}
    for m in _HTML_HEAD_RE.finditer(html[:8192]):
        found.setdefault(m.group(0)[:5].lower(), m)
    at = next((found[tag] for tag in (b"<head", b"<html", b"<!doc") if tag in found), None)
    cut = at.end() if at is not None else 0
    return html[:cut] + PANEL_ERROR_SCRIPT.encode("utf-8") + html[cut:]


def try_serve_plugin_ui_kit(handler: Any, rel_path: str) -> bool:
    """Serve ``/plugin-ui/_kit/<file>`` (ducky.css, ducky.js). Returns True when handled."""
    match = PLUGIN_UI_KIT_ROUTE_RE.match(rel_path)
    if not match:
        return False
    from backend.uefn_plugins.ui_kit import FILES

    found = FILES.get(match.group(1).lower())
    if found is None:
        send_plugin_ui_error(handler, 404)
        return True
    text, mime = found
    _send_plugin_ui_bytes(handler, text.encode("utf-8"), mime)
    return True


def try_serve_plugin_ui(handler: Any, rel_path: str) -> bool:
    """Serve ``/plugin-ui/<id>/...`` (or the shared UI kit) if ``rel_path`` matches.
    Returns True when handled. HTML pages get the panel error reporter first."""
    if try_serve_plugin_ui_kit(handler, rel_path):
        return True
    match = PLUGIN_UI_ROUTE_RE.match(rel_path)
    if not match:
        return False

    file_path = resolve_plugin_ui_file(match.group(1), match.group(2))
    if file_path is None:
        send_plugin_ui_error(handler, 404)
        return True

    try:
        data = file_path.read_bytes()
    except OSError:
        send_plugin_ui_error(handler, 404)
        return True

    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    if mime.split(";", 1)[0].strip().lower() == "text/html":
        data = with_panel_error_script(data)
    _send_plugin_ui_bytes(handler, data, mime)
    return True


def _send_plugin_ui_bytes(handler: Any, data: bytes, mime: str) -> None:
    # Opaque-origin iframes need CORS to load sibling assets if they ever use fetch;
    # for classic <script src> / <link> same-document loads this is unused but harmless.
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    csp = plugin_ui_csp(mime)
    if csp:
        handler.send_header("Content-Security-Policy", csp)
    # Deny framing from anywhere except our loopback panel (defense in depth;
    # primary isolation is iframe sandbox without allow-same-origin).
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)


def try_serve_user_sound(handler: Any, rel_path: str) -> bool:
    """Serve ``/user-sounds/<filename>`` from AppData/sounds. Returns True when handled."""
    match = USER_SOUNDS_ROUTE_RE.match(rel_path)
    if not match:
        return False

    from backend.skills.store import appdata_dir

    name = unquote(match.group(1) or "")
    if not name or "/" in name or "\\" in name or name in (".", "..") or ".." in name:
        handler.send_error(404)
        return True
    ext = Path(name).suffix.lower()
    if ext not in _SOUND_EXTS:
        handler.send_error(404)
        return True

    root = (appdata_dir() / "sounds").resolve()
    try:
        target = (root / name).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        handler.send_error(404)
        return True
    if not target.is_file():
        handler.send_error(404)
        return True

    try:
        data = target.read_bytes()
    except OSError:
        handler.send_error(404)
        return True

    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)
    return True


def panel_post_origin_allowed(
    origin: str | None, panel_origin: str, request_host: str | None = None
) -> bool:
    """True when a POST to ``/__panel_*`` is allowed.

    - Missing Origin (same-origin / non-browser clients) → allow.
    - Opaque origin from sandboxed iframes (``null``) → reject.
    - Panel UI origin → allow.
    - Origin host equal to the request Host (Cloudflare tunnel) → allow.
    """
    if origin is None or origin == "":
        return True
    if origin == "null":
        return False
    if origin.rstrip("/") == panel_origin.rstrip("/"):
        return True
    if request_host:
        from urllib.parse import urlparse as _urlparse

        origin_host = (_urlparse(origin).netloc or "").strip().lower()
        req = request_host.strip().lower()
        return bool(origin_host) and origin_host == req
    return False
