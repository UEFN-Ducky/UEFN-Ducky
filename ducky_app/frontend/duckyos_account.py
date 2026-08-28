"""DuckyOS tenant account login for the UEFN Ducky desktop app.

Opens the system browser to the tenant (default ``https://uefnducky.org``).
If you are already signed in there, the page mints a ``dky_v1_`` device API key
(scope ``uefn-ducky.app``), parks it server-side, and redirects to a local
``http://127.0.0.1`` callback with a one-time code. The app exchanges that
code over HTTPS (PKCE) for the key. Credentials are stored DPAPI-encrypted in
``credentials.dat``. Passwords never enter the app.
"""

from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import platform
import re
import secrets as secrets_mod
import socket
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from frontend import __version__

DEFAULT_BASE_URL = "https://uefnducky.org"
DEVICE_SCOPE = "uefn-ducky.app"
_CREDENTIALS_KEY = "duckyos_account"
_USER_AGENT = f"UEFN-Ducky/{__version__}"

_SESSION_COOKIE_RE = re.compile(r"^duckyos_session(?:_[^=]*)?$", re.I)
_CSRF_COOKIE_RE = re.compile(r"^duckyos_csrf(?:_[^=]*)?$", re.I)
_CHALLENGE_COOKIE_RE = re.compile(r"^duckyos_login_challenge(?:_[^=]*)?$", re.I)


class DuckyOSAccountError(Exception):
    """Raised for user-facing auth failures."""

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_base_url(raw: str | None) -> str:
    text = (raw or "").strip().rstrip("/")
    if not text:
        return DEFAULT_BASE_URL
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https":
        raise DuckyOSAccountError("Tenant URL must use https://", code="invalid_url")
    if not parsed.netloc:
        raise DuckyOSAccountError("Tenant URL is missing a host", code="invalid_url")
    return f"https://{parsed.netloc}"


def resolve_base_url(override: str | None = None) -> str:
    if override is not None and str(override).strip():
        return normalize_base_url(str(override))
    try:
        from frontend.settings import PanelSettings

        stored = (PanelSettings.load().duckyos_base_url or "").strip()
        if stored:
            base = normalize_base_url(stored)
            # Legacy platform subdomain — always use the primary product host.
            if (urlparse(base).hostname or "").lower() == "uefn-ducky.duckyos.org":
                return DEFAULT_BASE_URL
            return base
    except Exception:
        pass
    return DEFAULT_BASE_URL


def _load_blob() -> dict[str, Any]:
    from backend.agent.secrets import get_key

    raw = get_key(_CREDENTIALS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_blob(data: dict[str, Any]) -> None:
    from backend.agent.secrets import clear_key, set_key

    cleaned = {k: v for k, v in data.items() if v not in (None, "", [], {})}
    if cleaned:
        set_key(_CREDENTIALS_KEY, json.dumps(cleaned, separators=(",", ":")))
    else:
        clear_key(_CREDENTIALS_KEY)


def _clear_blob() -> None:
    from backend.agent.secrets import clear_key

    clear_key(_CREDENTIALS_KEY)


def pkce_pair() -> tuple[str, str]:
    """RFC 7636 S256: ``(code_verifier, code_challenge)``."""
    verifier = secrets_mod.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _clear_expired_auth() -> None:
    """Device key 401 — drop local credentials so the UI asks for sign-in."""
    try:
        stop_presence_heartbeat()
    except Exception:
        pass
    _clear_blob()


def _parse_set_cookie(headers: Any) -> dict[str, str]:
    """Extract name→value from Set-Cookie headers (urllib / http.client)."""
    out: dict[str, str] = {}
    raw_list: list[str] = []
    if hasattr(headers, "get_all"):
        raw_list = headers.get_all("Set-Cookie") or headers.get_all("set-cookie") or []
    elif hasattr(headers, "getlist"):
        raw_list = headers.getlist("Set-Cookie") or []
    else:
        single = headers.get("Set-Cookie") or headers.get("set-cookie")
        if single:
            raw_list = [single]
    for raw in raw_list:
        part = str(raw).split(";", 1)[0].strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        out[name.strip()] = value.strip()
    return out


def _pick_cookie(cookies: dict[str, str], pattern: re.Pattern[str]) -> tuple[str, str] | None:
    for name, value in cookies.items():
        if pattern.match(name) and value:
            return name, value
    return None


def _cookie_header(session_name: str, session_value: str, csrf_name: str = "", csrf_value: str = "", extra: dict[str, str] | None = None) -> str:
    parts: list[str] = []
    if session_name and session_value:
        parts.append(f"{session_name}={session_value}")
    if csrf_name and csrf_value:
        parts.append(f"{csrf_name}={csrf_value}")
    if extra:
        for k, v in extra.items():
            if k and v:
                parts.append(f"{k}={v}")
    return "; ".join(parts)


def _request(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    cookie_header: str = "",
    csrf_token: str = "",
    timeout: float = 20.0,
    follow_redirects: bool = True,
) -> tuple[int, dict[str, str], dict[str, Any] | None, str]:
    """Return ``(status, set_cookies, json_body_or_none, raw_text)``."""
    data = None
    headers = {
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
        "User-Agent": _USER_AGENT,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie_header:
        headers["Cookie"] = cookie_header
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    # Prefer CookieJar so urllib can follow redirects while keeping cookies when needed.
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
            set_cookies = _parse_set_cookie(resp.headers)
            # Also harvest cookies the jar collected across redirects.
            for cookie in jar:
                set_cookies.setdefault(cookie.name, cookie.value)
            csrf_hdr = resp.headers.get("X-Ducky-Csrf-Token") or resp.headers.get("x-ducky-csrf-token")
            if csrf_hdr and "duckyos_csrf" not in set_cookies:
                # Prefer cookie value when present; header is a fallback exposure.
                set_cookies.setdefault("duckyos_csrf", str(csrf_hdr).strip())
            parsed: dict[str, Any] | None = None
            if raw.strip().startswith(("{", "[")):
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        parsed = obj
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            return int(status), set_cookies, parsed, raw
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        set_cookies = _parse_set_cookie(exc.headers) if exc.headers else {}
        parsed = None
        if raw.strip().startswith(("{", "[")):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    parsed = obj
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        return int(exc.code), set_cookies, parsed, raw
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise DuckyOSAccountError(f"Network error: {exc}", code="network") from exc


def _ensure_csrf(base_url: str, blob: dict[str, Any]) -> dict[str, Any]:
    """Fetch ``/login`` to obtain a CSRF cookie when missing (needed for login/code)."""
    if blob.get("csrf_value") and blob.get("csrf_name"):
        return blob
    status, cookies, _body, _raw = _request("GET", f"{base_url}/login", timeout=15.0)
    if status >= 400 and status != 302:
        # Still try to harvest cookies from a soft failure.
        pass
    csrf = _pick_cookie(cookies, _CSRF_COOKIE_RE)
    if csrf:
        blob["csrf_name"], blob["csrf_value"] = csrf
        # Prefer explicit header-style exposure if jar renamed oddly
    elif cookies.get("duckyos_csrf"):
        blob["csrf_name"] = "duckyos_csrf"
        blob["csrf_value"] = cookies["duckyos_csrf"]
    return blob


def _device_key_name() -> str:
    host = ""
    try:
        host = socket.gethostname() or platform.node() or ""
    except Exception:
        host = ""
    host = re.sub(r"[^\w.\- ]+", "", host).strip() or "this-pc"
    return f"UEFN Ducky on {host[:48]}"


def _apply_session_cookies(blob: dict[str, Any], cookies: dict[str, str]) -> None:
    session = _pick_cookie(cookies, _SESSION_COOKIE_RE)
    if session:
        blob["session_name"], blob["session_value"] = session
    csrf = _pick_cookie(cookies, _CSRF_COOKIE_RE)
    if csrf:
        blob["csrf_name"], blob["csrf_value"] = csrf
    elif cookies.get("duckyos_csrf") and not blob.get("csrf_value"):
        blob["csrf_name"] = "duckyos_csrf"
        blob["csrf_value"] = cookies["duckyos_csrf"]
    challenge = _pick_cookie(cookies, _CHALLENGE_COOKIE_RE)
    if challenge:
        blob["challenge_name"], blob["challenge_value"] = challenge


def _session_cookie_header(blob: dict[str, Any], *, include_challenge: bool = False) -> str:
    extra: dict[str, str] = {}
    if include_challenge and blob.get("challenge_name") and blob.get("challenge_value"):
        extra[str(blob["challenge_name"])] = str(blob["challenge_value"])
    return _cookie_header(
        str(blob.get("session_name") or ""),
        str(blob.get("session_value") or ""),
        str(blob.get("csrf_name") or ""),
        str(blob.get("csrf_value") or ""),
        extra=extra or None,
    )


def mint_device_key(blob: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mint a ``dky_v1_`` key with ``uefn-ducky.app``. Soft-fails if scope missing."""
    blob = dict(blob or _load_blob())
    base = str(blob.get("base_url") or "").rstrip("/")
    if not base or not blob.get("session_value"):
        return blob
    body = {"name": _device_key_name(), "permissions": [DEVICE_SCOPE], "expiresInDays": 90}
    status, cookies, payload, raw = _request(
        "POST",
        f"{base}/api/auth/api-keys",
        body=body,
        cookie_header=_session_cookie_header(blob),
        csrf_token=str(blob.get("csrf_value") or ""),
    )
    _apply_session_cookies(blob, cookies)
    if status in (200, 201) and isinstance(payload, dict) and payload.get("token"):
        blob["device_key"] = str(payload["token"])
        blob["device_key_id"] = str(payload.get("id") or "")
        blob["device_key_error"] = ""
        return blob
    # Soft-fail: plugin scope may not be deployed yet.
    err = ""
    if isinstance(payload, dict):
        err = str(payload.get("error") or payload.get("message") or "")
    if not err:
        err = raw.strip()[:200] or f"HTTP {status}"
    blob["device_key_error"] = err
    return blob


def _revoke_device_key(blob: dict[str, Any]) -> None:
    key_id = str(blob.get("device_key_id") or "").strip()
    base = str(blob.get("base_url") or "").rstrip("/")
    if not key_id or not base or not blob.get("session_value"):
        return
    try:
        _request(
            "DELETE",
            f"{base}/api/auth/api-keys/{key_id}",
            cookie_header=_session_cookie_header(blob),
            csrf_token=str(blob.get("csrf_value") or ""),
            timeout=12.0,
        )
    except DuckyOSAccountError:
        pass


def fetch_me(blob: dict[str, Any] | None = None) -> dict[str, Any]:
    blob = dict(blob or _load_blob())
    base = str(blob.get("base_url") or "").rstrip("/")
    if not base or not blob.get("session_value"):
        raise DuckyOSAccountError("Not logged in", code="not_logged_in")
    status, cookies, payload, _raw = _request(
        "GET",
        f"{base}/api/auth/me",
        cookie_header=_session_cookie_header(blob),
    )
    _apply_session_cookies(blob, cookies)
    if status == 401:
        raise DuckyOSAccountError("Session expired — log in again", code="session_expired")
    if status != 200 or not isinstance(payload, dict):
        raise DuckyOSAccountError(f"Could not load account (HTTP {status})", code="me_failed")
    blob["email"] = str(payload.get("email") or blob.get("email") or "")
    blob["user_id"] = str(payload.get("userId") or payload.get("user_id") or "")
    blob["display_name"] = str(payload.get("displayName") or payload.get("display_name") or "")
    roles = payload.get("roles")
    if isinstance(roles, list):
        blob["roles"] = [str(r) for r in roles]
    return blob


def fetch_permissions(blob: dict[str, Any] | None = None) -> dict[str, Any]:
    blob = dict(blob or _load_blob())
    base = str(blob.get("base_url") or "").rstrip("/")
    if not base or not blob.get("session_value"):
        return blob
    status, cookies, payload, _raw = _request(
        "GET",
        f"{base}/api/acl/self",
        cookie_header=_session_cookie_header(blob),
    )
    _apply_session_cookies(blob, cookies)
    if status == 200 and isinstance(payload, dict):
        perms = payload.get("permissions")
        if isinstance(perms, list):
            blob["permissions"] = [str(p) for p in perms]
    return blob


def _persist_base_url(base: str) -> None:
    try:
        from frontend.settings import PanelSettings

        s = PanelSettings.load()
        if (s.duckyos_base_url or "").strip().rstrip("/") != base:
            s.duckyos_base_url = base
            s.save()
    except Exception:
        pass


def _finish_login(blob: dict[str, Any], email_fallback: str = "") -> dict[str, Any]:
    blob.pop("challenge_name", None)
    blob.pop("challenge_value", None)
    blob.pop("pending_code", None)
    blob.pop("browser_pending", None)
    if email_fallback and not blob.get("email"):
        blob["email"] = email_fallback
    if blob.get("session_value"):
        try:
            blob = fetch_me(blob)
        except DuckyOSAccountError:
            pass
        try:
            blob = fetch_permissions(blob)
        except DuckyOSAccountError:
            pass
        if not blob.get("device_key"):
            blob = mint_device_key(blob)
    _save_blob(blob)
    return blob


_BROWSER_LOGIN_LOCK = __import__("threading").Lock()
_BROWSER_LOGIN_CANCEL = __import__("threading").Event()


def cancel_browser_login() -> dict[str, Any]:
    """Signal a waiting ``start_browser_login`` to abort."""
    _BROWSER_LOGIN_CANCEL.set()
    return get_status()


def _browser_callback_page(*, ok: bool, email: str = "") -> str:
    """Styled localhost handoff page (inline CSS — no external assets)."""
    title = "Connected" if ok else "Login failed"
    email_line = ""
    if ok and email.strip():
        email_line = (
            f'<p class="email">Signed in as <strong>{html_lib.escape(email.strip())}</strong></p>'
        )
    body = (
        "You can close this tab and return to UEFN Ducky."
        if ok
        else "State mismatch or missing code. Close this tab and try again from the app."
    )
    tone = "ok" if ok else "err"
    mark = "✓" if ok else "!"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — UEFN Ducky</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0a0a0a;
    --card: #141414;
    --border: #2a2a2a;
    --text: #f4f4f5;
    --muted: #a1a1aa;
    --accent: #2563eb;
    --ok: #22c55e;
    --err: #ef4444;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background:
      radial-gradient(900px 420px at 15% 0%, rgba(37,99,235,.22), transparent 55%),
      radial-gradient(700px 380px at 90% 100%, rgba(37,99,235,.12), transparent 50%),
      var(--bg);
    color: var(--text);
  }}
  .card {{
    width: min(420px, calc(100vw - 2rem));
    padding: 2rem 1.75rem 1.75rem;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: linear-gradient(180deg, #171717 0%, var(--card) 100%);
    box-shadow: 0 24px 60px rgba(0,0,0,.45);
    text-align: center;
  }}
  .brand {{
    font-size: .75rem; letter-spacing: .14em; text-transform: uppercase;
    color: var(--muted); margin: 0 0 1.25rem;
  }}
  .mark {{
    width: 3.25rem; height: 3.25rem; margin: 0 auto 1rem; border-radius: 999px;
    display: grid; place-items: center; font-size: 1.35rem; font-weight: 700;
  }}
  .mark.ok {{ background: rgba(34,197,94,.15); color: var(--ok); border: 1px solid rgba(34,197,94,.35); }}
  .mark.err {{ background: rgba(239,68,68,.15); color: var(--err); border: 1px solid rgba(239,68,68,.35); }}
  h1 {{ margin: 0 0 .5rem; font-size: 1.5rem; font-weight: 650; letter-spacing: -.02em; }}
  p {{ margin: 0; line-height: 1.5; color: var(--muted); font-size: .95rem; }}
  p.email {{ margin-top: .85rem; color: var(--text); }}
  .hint {{
    margin-top: 1.35rem; padding-top: 1rem; border-top: 1px solid var(--border);
    font-size: .8rem; color: var(--muted);
  }}
  .hint span {{ color: var(--accent); }}
</style>
</head>
<body>
  <main class="card">
    <p class="brand">UEFN Ducky</p>
    <div class="mark {tone}" aria-hidden="true">{mark}</div>
    <h1>{title}</h1>
    <p>{html_lib.escape(body)}</p>
    {email_line}
    <p class="hint">Return to <span>UEFN Ducky</span> — this tab can be closed.</p>
  </main>
</body>
</html>"""


def start_browser_login(base_url: str = "", *, timeout_secs: float = 300.0) -> dict[str, Any]:
    """
    Secure desktop login: open the system browser to the tenant; if already
    signed in there, the page mints a device key and redirects to a local
    ``http://127.0.0.1`` callback. No password is typed in the app.
    """
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse as _urlparse

    base = normalize_base_url(base_url or resolve_base_url())
    _persist_base_url(base)

    state = secrets_mod.token_hex(16)
    verifier, challenge = pkce_pair()
    result: dict[str, Any] = {"done": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = _urlparse(self.path)
            if parsed.path.rstrip("/") != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            got_state = (qs.get("state") or [""])[0]
            code = (qs.get("code") or [""])[0]
            # ponytail: token= still accepted for one release while tenants update.
            token = (qs.get("token") or [""])[0]
            key_id = (qs.get("key_id") or [""])[0]
            email = (qs.get("email") or [""])[0]
            ok = got_state == state and (bool(code) or token.startswith("dky_v1_"))
            body = _browser_callback_page(ok=ok, email=email if ok else "")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            if ok:
                result.update(
                    {
                        "done": True,
                        "ok": True,
                        "code": code,
                        "token": token,
                        "key_id": key_id,
                        "email": email,
                    }
                )
            else:
                result.update({"done": True, "ok": False, "error": "Invalid callback"})

    if not _BROWSER_LOGIN_LOCK.acquire(blocking=False):
        raise DuckyOSAccountError("A browser login is already in progress", code="busy")

    _BROWSER_LOGIN_CANCEL.clear()
    httpd: HTTPServer | None = None
    try:
        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        port = int(httpd.server_address[1])
        auth_url = (
            f"{base}/admin/plugins/uefn-ducky/desktop-auth"
            f"?q={state}.{port}&challenge={challenge}"
        )

        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.25}, daemon=True)
        thread.start()
        webbrowser.open(auth_url)

        deadline = __import__("time").monotonic() + max(30.0, float(timeout_secs))
        while __import__("time").monotonic() < deadline:
            if _BROWSER_LOGIN_CANCEL.is_set():
                raise DuckyOSAccountError("Browser login cancelled", code="cancelled")
            if result.get("done"):
                break
            __import__("time").sleep(0.2)

        if not result.get("done") or not result.get("ok"):
            if _BROWSER_LOGIN_CANCEL.is_set():
                raise DuckyOSAccountError("Browser login cancelled", code="cancelled")
            raise DuckyOSAccountError(
                str(result.get("error") or "Timed out waiting for browser login"),
                code="browser_timeout",
            )

        code = str(result.get("code") or "")
        token = str(result.get("token") or "")
        key_id = str(result.get("key_id") or "")
        email = str(result.get("email") or "")
        if code:
            payload = _plugin_collect(
                "uefn-ducky",
                "desktop-exchange",
                {"code": code, "codeVerifier": verifier, "state": state},
                unavailable_code="auth_unavailable",
                unavailable_msg="Desktop login plugin is not active on this tenant yet.",
                error_code="pkce_exchange_failed",
                allow_anonymous=True,
                timeout=20.0,
            )
            token = str(payload.get("token") or "")
            key_id = str(payload.get("keyId") or payload.get("key_id") or key_id)
            email = str(payload.get("email") or email)
        if not token.startswith("dky_v1_"):
            raise DuckyOSAccountError("Login handoff did not return a device key", code="pkce_exchange_failed")
        blob: dict[str, Any] = {
            "base_url": base,
            "email": email,
            "device_key": token,
            "device_key_id": key_id,
            "device_key_error": "",
        }
        _save_blob(blob)
        start_presence_heartbeat()
        status = get_status()
        status["ok"] = True
        return status
    finally:
        try:
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
        except Exception:
            pass
        _BROWSER_LOGIN_LOCK.release()


def logout() -> dict[str, Any]:
    _BROWSER_LOGIN_CANCEL.set()
    stop_presence_heartbeat()
    blob = _load_blob()
    base = str(blob.get("base_url") or "").rstrip("/")
    if base and blob.get("session_value"):
        _revoke_device_key(blob)
        try:
            _request(
                "POST",
                f"{base}/api/auth/logout",
                cookie_header=_session_cookie_header(blob),
                csrf_token=str(blob.get("csrf_value") or ""),
                timeout=10.0,
            )
        except DuckyOSAccountError:
            pass
    _clear_blob()
    return get_status()


def get_status() -> dict[str, Any]:
    """Public status for the UI — never includes session/device secrets."""
    blob = _load_blob()
    base = str(blob.get("base_url") or resolve_base_url())
    device_active = bool(blob.get("device_key"))
    session_ok = bool(blob.get("session_value"))
    logged_in = device_active or session_ok
    return {
        "logged_in": logged_in,
        "needs_code": False,
        "browser_pending": False,
        "base_url": base,
        "default_base_url": DEFAULT_BASE_URL,
        "email": str(blob.get("email") or "") if logged_in else "",
        "display_name": str(blob.get("display_name") or "") if logged_in else "",
        "user_id": str(blob.get("user_id") or "") if logged_in else "",
        "roles": list(blob.get("roles") or []) if logged_in else [],
        "permissions": list(blob.get("permissions") or []) if logged_in else [],
        "device_key_active": device_active if logged_in else False,
        "device_key_error": str(blob.get("device_key_error") or "") if logged_in else "",
        "auth_mode": "browser",
    }


def refresh_status() -> dict[str, Any]:
    """Refresh identity when a session cookie exists; device-key-only stays as-is."""
    blob = _load_blob()
    if blob.get("session_value"):
        try:
            blob = fetch_me(blob)
            blob = fetch_permissions(blob)
            _save_blob(blob)
        except DuckyOSAccountError as exc:
            if exc.code == "session_expired":
                kept = {
                    "base_url": blob.get("base_url"),
                    "email": blob.get("email"),
                    "device_key": blob.get("device_key"),
                    "device_key_id": blob.get("device_key_id"),
                }
                _save_blob({k: v for k, v in kept.items() if v})
                status = get_status()
                if not status.get("logged_in"):
                    status["session_expired"] = True
                    status["error"] = exc.message
                return status
            status = get_status()
            status["error"] = exc.message
            return status
    return get_status()


def api_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    prefer_bearer: bool = True,
    allow_anonymous: bool = False,
    timeout: float = 20.0,
) -> tuple[int, dict[str, Any] | None, str]:
    """
    Authenticated request helper for future online features.

    Prefers ``Authorization: Bearer dky_v1_…`` (device key from browser login).
    Falls back to session cookie + CSRF when present. With ``allow_anonymous``
    the request goes out unauthenticated (public endpoints like the Store
    catalog) instead of raising ``not_logged_in``.
    """
    blob = _load_blob()
    base = str(blob.get("base_url") or "").rstrip("/")
    if not base:
        if not allow_anonymous:
            raise DuckyOSAccountError("Not logged in", code="not_logged_in")
        base = resolve_base_url()
    path = path if path.startswith("/") else f"/{path}"
    url = f"{base}{path}"

    headers_cookie = ""
    csrf = ""
    extra_headers: dict[str, str] = {}
    # Collect endpoints require Origin to match Host (browser CSRF). Desktop
    # urllib sends neither Origin nor Sec-Fetch-Site → 403 forbidden without this.
    extra_headers["Origin"] = base
    if prefer_bearer and blob.get("device_key"):
        extra_headers["Authorization"] = f"Bearer {blob['device_key']}"
    elif blob.get("session_value"):
        headers_cookie = _session_cookie_header(blob)
        csrf = str(blob.get("csrf_value") or "")
    elif blob.get("device_key"):
        extra_headers["Authorization"] = f"Bearer {blob['device_key']}"
    elif not allow_anonymous:
        raise DuckyOSAccountError("Not logged in", code="not_logged_in")

    data = None
    req_headers = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        **extra_headers,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    if headers_cookie:
        req_headers["Cookie"] = headers_cookie
    if csrf and method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        req_headers["X-CSRF-Token"] = csrf

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", None) or resp.getcode()
            parsed = None
            if raw.strip().startswith(("{", "[")):
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        parsed = obj
                except (TypeError, ValueError, json.JSONDecodeError):
                    parsed = None
            return int(status), parsed, raw
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        parsed = None
        if raw.strip().startswith(("{", "[")):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    parsed = obj
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        if int(exc.code) == 401 and (blob.get("device_key") or blob.get("session_value")):
            _clear_expired_auth()
            raise DuckyOSAccountError("Session expired — log in again", code="session_expired") from exc
        return int(exc.code), parsed, raw
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise DuckyOSAccountError(f"Network error: {exc}", code="network") from exc


_PRESENCE_STOP = __import__("threading").Event()
_PRESENCE_THREAD: Any = None
_PRESENCE_LOCK = __import__("threading").Lock()
_PRESENCE_INTERVAL_S = 90.0


def _presence_project_label() -> str:
    try:
        from frontend.settings import PanelSettings

        root = (PanelSettings.load().uefn_project_root or "").strip()
        if not root:
            return ""
        return root.replace("\\", "/").rstrip("/").split("/")[-1][:120]
    except Exception:
        return ""


def _presence_uefn_online() -> bool:
    try:
        from backend.bridge import listener_get_health
        from frontend.settings import PANEL_LISTENER_PORT

        health = listener_get_health(PANEL_LISTENER_PORT)
        return bool(health is not None and health.get("status") == "ok")
    except Exception:
        return False


def send_presence_heartbeat() -> bool:
    """POST presence to uefn-ducky-store collect (teams hub). Returns False if not logged in / skipped."""
    blob = _load_blob()
    if not (blob.get("device_key") or blob.get("session_value")):
        return False
    email = str(blob.get("email") or "").strip()
    body: dict[str, Any] = {
        "source": "desktop",
        "email": email,
        "projectLabel": _presence_project_label(),
        "uefnOnline": _presence_uefn_online(),
        "appVersion": __version__,
    }
    try:
        status, _parsed, _raw = api_request(
            "POST",
            "/api/plugins/uefn-ducky-store/collect/presence",
            body,
            prefer_bearer=True,
            timeout=12.0,
        )
        return 200 <= int(status) < 300
    except DuckyOSAccountError:
        return False
    except Exception:
        return False


def start_presence_heartbeat() -> None:
    """Daemon thread: while logged in, ping team presence about once a minute."""
    global _PRESENCE_THREAD
    with _PRESENCE_LOCK:
        if _PRESENCE_THREAD is not None and _PRESENCE_THREAD.is_alive():
            return
        if not (_load_blob().get("device_key") or _load_blob().get("session_value")):
            return
        _PRESENCE_STOP.clear()

        def _loop() -> None:
            while not _PRESENCE_STOP.is_set():
                try:
                    send_presence_heartbeat()
                except Exception:
                    pass
                _PRESENCE_STOP.wait(_PRESENCE_INTERVAL_S)

        _PRESENCE_THREAD = __import__("threading").Thread(
            target=_loop, daemon=True, name="duckyos-presence"
        )
        _PRESENCE_THREAD.start()


def stop_presence_heartbeat() -> None:
    _PRESENCE_STOP.set()


def _plugin_collect(
    plugin_id: str,
    event: str,
    body: dict[str, Any] | None = None,
    *,
    unavailable_code: str,
    unavailable_msg: str,
    error_code: str,
    allow_anonymous: bool = False,
    timeout: float = 20.0,
) -> dict[str, Any]:
    status, parsed, raw = api_request(
        "POST",
        f"/api/plugins/{plugin_id}/collect/{event}",
        body or {},
        prefer_bearer=True,
        allow_anonymous=allow_anonymous,
        timeout=timeout,
    )
    if status == 404:
        raise DuckyOSAccountError(unavailable_msg, code=unavailable_code)
    if not (200 <= int(status) < 300):
        err = ""
        if isinstance(parsed, dict):
            err = str(parsed.get("error") or "")
            payload = parsed.get("payload")
            if isinstance(payload, dict) and payload.get("error"):
                err = str(payload["error"])
        raise DuckyOSAccountError(
            err or raw or f"{plugin_id} request failed ({status})",
            code=error_code,
        )
    if not isinstance(parsed, dict):
        return {}
    payload = parsed.get("payload")
    return payload if isinstance(payload, dict) else parsed


def _collect_payload(event: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    # Teams live inside uefn-ducky-store (merged from former uefn-teams plugin).
    return _plugin_collect(
        "uefn-ducky-store",
        event,
        body,
        unavailable_code="teams_unavailable",
        unavailable_msg="Ducky Store (teams) is not active on this tenant yet.",
        error_code="teams_error",
    )


def _store_collect(
    event: str,
    body: dict[str, Any] | None = None,
    *,
    allow_anonymous: bool = False,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return _plugin_collect(
        "uefn-ducky-store",
        event,
        body,
        unavailable_code="store_unavailable",
        unavailable_msg="Ducky Store plugin is not active on this tenant yet.",
        error_code="store_error",
        allow_anonymous=allow_anonymous,
        timeout=timeout,
    )


def _is_online(presence: Any, *, stale_seconds: int = 120) -> bool:
    if not isinstance(presence, dict):
        return False
    last = str(presence.get("last_seen") or "")
    if not last:
        return False
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return age <= float(stale_seconds)
    except Exception:
        return False


def teams_snapshot(*, stale_seconds: int = 120) -> dict[str, Any]:
    """Hub snapshot for the Account tab (one collect call — presence is upserted inside hub)."""
    blob = _load_blob()
    if not (blob.get("device_key") or blob.get("session_value")):
        raise DuckyOSAccountError("Not logged in", code="not_logged_in")
    email = str(blob.get("email") or "").strip()
    # Include desktop presence fields so hub's internal upsert stays useful.
    body = {
        "email": email,
        "source": "desktop",
        "staleSeconds": int(stale_seconds),
        "projectLabel": _presence_project_label(),
        "uefnOnline": _presence_uefn_online(),
        "appVersion": __version__,
    }
    try:
        hub = _collect_payload("hub", body)
    except DuckyOSAccountError as exc:
        return {
            "ok": False,
            "error": exc.message,
            "code": exc.code,
            "teams": [],
            "needs_team": True,
            "online": [],
            "teams_url": _teams_site_url("/teams"),
        }

    teams_raw = hub.get("teams") if isinstance(hub.get("teams"), list) else []
    teams: list[dict[str, Any]] = []
    online_by_id: dict[str, dict[str, Any]] = {}
    self_email = email.lower()
    for entry in teams_raw:
        if not isinstance(entry, dict):
            continue
        team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
        members_out: list[dict[str, Any]] = []
        for mem in entry.get("members") or []:
            if not isinstance(mem, dict):
                continue
            presence = mem.get("presence") if isinstance(mem.get("presence"), dict) else {}
            profile = mem.get("profile") if isinstance(mem.get("profile"), dict) else {}
            user_id = str(mem.get("userId") or "")
            mem_email = str(mem.get("email") or "")
            display = str(profile.get("display_name") or mem_email or user_id or "member")
            online = _is_online(presence, stale_seconds=stale_seconds)
            members_out.append(
                {
                    "user_id": user_id,
                    "email": mem_email,
                    "role": str(mem.get("role") or "member"),
                    "display_name": display,
                    "online": online,
                    "presence": {
                        "source": str(presence.get("source") or ""),
                        "project_label": str(presence.get("project_label") or ""),
                        "uefn_online": bool(presence.get("uefn_online")),
                        "last_seen": str(presence.get("last_seen") or ""),
                    },
                }
            )
            if online and user_id and user_id not in online_by_id:
                online_by_id[user_id] = {
                    "user_id": user_id,
                    "display_name": display,
                    "source": str(presence.get("source") or ""),
                    "project_label": str(presence.get("project_label") or ""),
                    "uefn_online": bool(presence.get("uefn_online")),
                    "is_self": bool(
                        mem_email.lower() == self_email
                        if self_email and mem_email
                        else False
                    ),
                }
        teams.append(
            {
                "id": str(team.get("id") or ""),
                "name": str(team.get("name") or ""),
                "slug": str(team.get("slug") or ""),
                "description": str(team.get("description") or ""),
                "my_role": str(entry.get("myRole") or "member"),
                "members": members_out,
            }
        )

    return {
        "ok": True,
        "teams": teams,
        "needs_team": bool(hub.get("needsTeam")) or not teams,
        "online": list(online_by_id.values()),
        "teams_url": _teams_site_url("/teams"),
        "invite_url": _teams_site_url("/invite"),
        "stale_seconds": int(stale_seconds),
    }


def _teams_site_url(path: str) -> str:
    blob = _load_blob()
    base = str(blob.get("base_url") or resolve_base_url()).rstrip("/")
    path = path if path.startswith("/") else f"/{path}"
    return f"{base}{path}"


def open_site_path(path: str = "/teams") -> dict[str, Any]:
    """Open a tenant website path in the system browser (teams create/manage UI lives there)."""
    import webbrowser

    url = _teams_site_url(path or "/teams")
    webbrowser.open(url)
    return {"ok": True, "url": url}


def _store_icon_fields_from_remote(raw: dict[str, Any]) -> dict[str, Any]:
    data_url = str(raw.get("icon_data_url") or "").strip() or None
    mime = str(raw.get("icon_mime") or "").strip() or None
    has_icon = bool(raw.get("has_icon")) or bool(data_url)
    try:
        install_count = int(raw.get("install_count") or 0)
    except (TypeError, ValueError):
        install_count = 0
    return {
        "install_count": install_count,
        "has_icon": has_icon,
        "icon_mime": mime,
        "icon_data_url": data_url,
    }


def _local_plugin_icon_fields(plug: dict[str, Any]) -> dict[str, Any]:
    """Read icon from an installed plugin folder when Store has no remote icon."""
    import base64

    root = Path(str(plug.get("path") or ""))
    if not root.is_dir():
        return {
            "install_count": None,
            "has_icon": False,
            "icon_mime": None,
            "icon_data_url": None,
        }
    candidates = (
        ("assets/icon.svg", "image/svg+xml"),
        ("assets/icon.png", "image/png"),
        ("assets/icon.jpg", "image/jpeg"),
        ("assets/icon.jpeg", "image/jpeg"),
        ("icon.svg", "image/svg+xml"),
        ("icon.png", "image/png"),
        ("icon.jpg", "image/jpeg"),
        ("icon.jpeg", "image/jpeg"),
    )
    for rel, mime in candidates:
        path = root / rel
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if not raw or len(raw) > 64 * 1024:
            # Still mark has_icon if file exists but is too large to embed.
            return {
                "install_count": None,
                "has_icon": True,
                "icon_mime": mime,
                "icon_data_url": None,
            }
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "install_count": None,
            "has_icon": True,
            "icon_mime": mime,
            "icon_data_url": f"data:{mime};base64,{b64}",
        }
    return {
        "install_count": None,
        "has_icon": False,
        "icon_mime": None,
        "icon_data_url": None,
    }


def _store_commerce_fields(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        price_cents = int(raw.get("price_cents") or 0)
    except (TypeError, ValueError):
        price_cents = 0
    currency = str(raw.get("currency") or "usd").strip().lower() or "usd"
    paid = bool(raw.get("paid")) if "paid" in raw else price_cents > 0
    owned_raw = raw.get("owned")
    owned: bool | None
    if owned_raw is None:
        owned = None
    else:
        owned = bool(owned_raw)
    return {
        "price_cents": price_cents,
        "currency": currency,
        "paid": paid,
        "owned": owned,
        "stripe_product_key": str(raw.get("stripe_product_key") or "") or None,
    }


def store_catalog() -> dict[str, Any]:
    """Published store items + local install/update state for skills and UEFN plugins.

    Anonymous-friendly: the catalog lists only published items server-side.
    When signed in, paid items include ``owned``.
    """
    try:
        payload = _store_collect("catalog", {}, allow_anonymous=True, timeout=20.0)
    except DuckyOSAccountError as exc:
        # Transient 403 right after a large download — one quiet retry.
        msg = (exc.message or "").lower()
        if "forbidden" in msg or exc.code in ("store_error", "forbidden"):
            try:
                __import__("time").sleep(0.45)
                payload = _store_collect("catalog", {}, allow_anonymous=True, timeout=20.0)
            except DuckyOSAccountError as retry_exc:
                return {
                    "ok": False,
                    "error": retry_exc.message,
                    "code": retry_exc.code,
                    "items": [],
                }
        else:
            return {
                "ok": False,
                "error": exc.message,
                "code": exc.code,
                "items": [],
            }

    installed_skills: dict[str, dict[str, Any]] = {}
    skill_denied: set[str] = set()
    try:
        from backend.skills.store import list_skill_packs
        from frontend.settings import PanelSettings

        skill_denied = set(PanelSettings.load().default_disabled_packs or [])
        for pack in list_skill_packs(include_text=False):
            pid = str(pack.get("id") or "")
            if pid:
                installed_skills[pid] = {
                    "version": int(pack.get("version") or 0),
                    "source": str(pack.get("source") or ""),
                    "store_slug": str(pack.get("store_slug") or ""),
                }
    except Exception:
        installed_skills = {}
        skill_denied = set()

    installed_plugins: dict[str, dict[str, Any]] = {}
    try:
        from backend.uefn_plugins.store import list_uefn_plugins

        for plug in list_uefn_plugins():
            pid = str(plug.get("id") or "")
            if pid:
                installed_plugins[pid] = plug
    except Exception:
        installed_plugins = {}

    items_out: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for raw in payload.get("items") if isinstance(payload.get("items"), list) else []:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug") or "")
        category = str(raw.get("category") or raw.get("kind") or "")
        kind = str(raw.get("kind") or category or "skill").lower()
        if kind in ("plugin", "plugins", "feature", "features"):
            kind = "plugin"
        elif kind in ("skill", "skills", ""):
            kind = "skill"
        categories = [
            str(c).strip().lower()
            for c in (raw.get("categories") if isinstance(raw.get("categories"), list) else [])
            if str(c).strip()
        ]
        tags = [
            str(t).strip()
            for t in (raw.get("tags") if isinstance(raw.get("tags"), list) else [])
            if str(t).strip()
        ]
        # Pack versions: legacy ints or semver (1.0.8). Rank for update checks.
        from backend.uefn_plugins.plugin_version import (
            format_plugin_version,
            plugin_version_rank,
        )

        latest = str(raw.get("latest_version") or "").strip()
        remote_rank = plugin_version_rank(latest or raw.get("pack_version") or 0)
        remote_display = format_plugin_version(latest or raw.get("pack_version") or 0)

        icon_fields = _store_icon_fields_from_remote(raw)
        commerce = _store_commerce_fields(raw)
        local_rank = None
        local_display = None
        enabled = None
        source = None
        if kind == "plugin":
            plug = installed_plugins.get(slug)
            if not categories and plug:
                categories = _plugin_browse_categories(plug)
            elif not categories:
                categories = ["plugins"]
            if plug:
                local_rank = plugin_version_rank(plug.get("version"))
                local_display = format_plugin_version(plug.get("version"))
                enabled = bool(plug.get("enabled"))
                source = str(plug.get("source") or "")
                if not icon_fields.get("icon_data_url") and not icon_fields.get("has_icon"):
                    local_icon = _local_plugin_icon_fields(plug)
                    icon_fields = {
                        **icon_fields,
                        "has_icon": bool(local_icon.get("has_icon")),
                        "icon_mime": local_icon.get("icon_mime") or icon_fields.get("icon_mime"),
                        "icon_data_url": local_icon.get("icon_data_url") or icon_fields.get("icon_data_url"),
                    }
                if source in ("local", "ai"):
                    # Store must not overwrite local/AI — show as installed, no update.
                    state = "installed"
                    items_out.append(
                        {
                            "slug": slug,
                            "category": category or "plugin",
                            "kind": "plugin",
                            "categories": categories,
                            "tags": tags,
                            "name": str(raw.get("name") or plug.get("label") or slug),
                            "description": str(raw.get("description") or plug.get("description") or ""),
                            "latest_version": latest or remote_display,
                            "pack_version": remote_rank,
                            "installed_version": local_display,
                            "enabled": enabled,
                            "source": source,
                            "state": state,
                            "contributes_summary": _plugin_contributes_summary(plug),
                            **commerce,
                            **icon_fields,
                        }
                    )
                    seen_slugs.add(slug)
                    continue
        else:
            # Skill is shipped inside an installed desktop plugin — hide the
            # standalone Store skill row so Update All cannot try (and fail) to
            # overwrite a plugin-owned pack (physics / virtualpointer).
            try:
                from backend.skills.store import plugin_owner_for_skill

                skill_owner = plugin_owner_for_skill(slug)
            except Exception:
                skill_owner = None
            if skill_owner and skill_owner in installed_plugins:
                continue
            sk = installed_skills.get(slug)
            if sk is not None:
                sk_ver = int(sk.get("version") or 0)
                local_rank = plugin_version_rank(sk_ver)
                local_display = format_plugin_version(sk_ver)
                # Catalog hit + installed → Store skill (pack may already stamp source).
                source = str(sk.get("source") or "") or "store"
                enabled = slug not in skill_denied
            if not categories:
                categories = ["skills"]

        state = "available"
        if local_rank is not None:
            if remote_rank > local_rank:
                state = "update"
            else:
                state = "installed"
        # Old clients that don't understand plugins still get a usable pill.
        if kind not in ("skill", "plugin"):
            state = "unsupported"
        contrib_summary: list[str] = []
        if kind == "plugin":
            plug = installed_plugins.get(slug)
            if plug:
                contrib_summary = _plugin_contributes_summary(plug)
        items_out.append(
            {
                "slug": slug,
                "category": category,
                "kind": kind,
                "categories": categories,
                "tags": tags,
                "name": str(raw.get("name") or slug),
                "description": str(raw.get("description") or ""),
                "latest_version": latest or remote_display,
                "pack_version": remote_rank,
                "installed_version": local_display,
                "enabled": enabled,
                "source": source,
                "state": state,
                "contributes_summary": contrib_summary,
                **commerce,
                **icon_fields,
            }
        )
        seen_slugs.add(slug)

    # Local-only / bundled plugins not in the remote catalog.
    from backend.uefn_plugins.plugin_version import (
        format_plugin_version as _fmt_pv,
        plugin_version_rank as _rank_pv,
    )

    for pid, plug in installed_plugins.items():
        if pid in seen_slugs:
            continue
        items_out.append(
            {
                "slug": pid,
                "category": "plugin",
                "kind": "plugin",
                "categories": _plugin_browse_categories(plug),
                "tags": [],
                "name": str(plug.get("label") or pid),
                "description": str(plug.get("description") or ""),
                "latest_version": _fmt_pv(plug.get("version")),
                "pack_version": _rank_pv(plug.get("version")),
                "installed_version": _fmt_pv(plug.get("version")),
                "enabled": bool(plug.get("enabled")),
                "source": str(plug.get("source") or "local"),
                "state": "installed",
                "price_cents": 0,
                "currency": "usd",
                "paid": False,
                "owned": True,
                "contributes_summary": _plugin_contributes_summary(plug),
                **_local_plugin_icon_fields(plug),
            }
        )
    return {"ok": True, "items": items_out}


def _plugin_browse_categories(plug: dict[str, Any]) -> list[str]:
    """Infer Store browse categories from contributes (themes / plugins / gateways)."""
    contrib = plug.get("contributes") if isinstance(plug.get("contributes"), dict) else {}
    has_theme = bool(
        contrib.get("appearance.profiles")
        or contrib.get("appearance_profiles")
        or contrib.get("appearance.effects")
        or contrib.get("appearance_effects")
        or contrib.get("appearance.skin")
        or contrib.get("appearance_skin")
        or contrib.get("appearance.css")
        or contrib.get("appearance_css")
    )
    has_gateway = bool(
        contrib.get("llm.providers")
        or contrib.get("llm_providers")
        or contrib.get("llm.coding_agents")
        or contrib.get("llm_coding_agents")
    )
    cats: list[str] = []
    if has_theme:
        cats.append("themes")
    if has_gateway:
        cats.append("gateways")
    # Browse UI hides the redundant "plugins" bucket — only add real tags.
    return cats


def _plugin_contributes_summary(plug: dict[str, Any]) -> list[str]:
    """Short labels for Store detail: themes, skills, sounds, tools, etc."""
    contrib = plug.get("contributes") if isinstance(plug.get("contributes"), dict) else {}
    out: list[str] = []
    if contrib.get("appearance.profiles") or contrib.get("appearance_profiles"):
        out.append("themes")
    if contrib.get("appearance.effects") or contrib.get("appearance_effects"):
        out.append("effects")
    if contrib.get("appearance.skin") or contrib.get("appearance_skin") or contrib.get("appearance.css") or contrib.get("appearance_css"):
        out.append("skin")
    if contrib.get("sounds"):
        out.append("sounds")
    if contrib.get("agent.tools") or contrib.get("agent_tools"):
        out.append("tools")
    if contrib.get("llm.providers") or contrib.get("llm_providers"):
        out.append("gateway")
    if contrib.get("ui.panels") or contrib.get("ui_panels") or contrib.get("settings.tabs") or contrib.get("settings_tabs"):
        out.append("ui")
    # Bundled skill folders under skills/<id>/SKILL.md (installed plugin path).
    # Also surface "tools" when api.tool() registered names at runtime.
    try:
        from pathlib import Path

        root = Path(str(plug.get("path") or ""))
        skills_root = root / "skills"
        if skills_root.is_dir():
            for child in sorted(skills_root.iterdir()):
                if child.is_dir() and (child / "SKILL.md").is_file():
                    out.append("skills")
                    break
        pid = str(plug.get("id") or "").strip()
        if pid and "tools" not in out:
            from backend.uefn_plugins.host import is_uefn_agent_tool_plugin

            if is_uefn_agent_tool_plugin(pid):
                out.append("tools")
    except Exception:
        pass
    # Dedupe preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for label in out:
        if label not in seen:
            seen.add(label)
            deduped.append(label)
    return deduped


def store_checkout(
    slug: str,
    *,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> dict[str, Any]:
    """Start Stripe Checkout for a paid Store item (requires DuckyOS sign-in).

    Spawns a short-lived localhost callback so Stripe can return the session id
    and we can ``grant`` ownership without relying solely on the site webhook.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse as _urlparse

    slug = (slug or "").strip()
    if not slug:
        raise DuckyOSAccountError("slug required", code="bad_request")
    base = resolve_base_url().rstrip("/")
    cancel = (cancel_url or "").strip() or f"{base}/"

    httpd_box: dict[str, Any] = {"httpd": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = _urlparse(self.path)
            if parsed.path.rstrip("/") != "/store-purchase":
                self.send_response(404)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            session_id = (qs.get("session_id") or qs.get("sessionId") or [""])[0]
            ok = False
            err = ""
            if session_id:
                try:
                    store_grant_purchase(session_id, slug=slug)
                    ok = True
                except Exception as exc:
                    err = str(exc)
            html = (
                "<!doctype html><html><body style='font-family:system-ui;padding:2rem'>"
                + (
                    "<h1>Purchase complete</h1><p>You can close this tab and return to UEFN Ducky.</p>"
                    if ok
                    else f"<h1>Purchase pending</h1><p>{err or 'Waiting for payment confirmation.'}</p>"
                )
                + "</body></html>"
            )
            raw = html.encode("utf-8")
            self.send_response(200 if ok else 202)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            try:
                threading.Thread(
                    target=lambda: (httpd_box["httpd"] and httpd_box["httpd"].shutdown()),
                    daemon=True,
                ).start()
            except Exception:
                pass

    success = (success_url or "").strip()
    if not success:
        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        httpd_box["httpd"] = httpd
        port = int(httpd.server_address[1])
        success = f"http://127.0.0.1:{port}/store-purchase?session_id={{CHECKOUT_SESSION_ID}}"
        threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.25},
            daemon=True,
        ).start()
        # Auto-stop the listener after 15 minutes.
        def _timeout_shutdown() -> None:
            __import__("time").sleep(900)
            try:
                httpd.shutdown()
            except Exception:
                pass

        threading.Thread(target=_timeout_shutdown, daemon=True).start()

    payload = _store_collect(
        "checkout",
        {
            "slug": slug,
            "successUrl": success,
            "cancelUrl": cancel,
            "siteBaseUrl": base,
        },
        allow_anonymous=False,
        timeout=30.0,
    )
    url = str(payload.get("url") or "").strip()
    if not url:
        raise DuckyOSAccountError("Checkout returned no URL", code="store_checkout")
    return {"ok": True, "url": url, "slug": slug}


def store_grant_purchase(session_id: str, *, slug: str | None = None) -> dict[str, Any]:
    """Confirm a paid Checkout session and record Store ownership."""
    session_id = (session_id or "").strip()
    if not session_id:
        raise DuckyOSAccountError("sessionId required", code="bad_request")
    body: dict[str, Any] = {"sessionId": session_id}
    if slug:
        body["slug"] = str(slug).strip()
    payload = _store_collect("grant", body, allow_anonymous=False, timeout=30.0)
    return {
        "ok": True,
        "slug": payload.get("slug"),
        "userId": payload.get("userId"),
        "alreadyOwned": bool(payload.get("alreadyOwned")),
    }


# Concurrent Store installs each call reload_plugins() — that freezes the panel UI.
# Serialize end-to-end so Update All stays responsive (JS also queues; this is the belt).
_STORE_INSTALL_LOCK = __import__("threading").Lock()


def store_download_and_install(
    slug: str,
    *,
    version: str | None = None,
    replace: bool = True,
    paid: bool | None = None,
    is_update: bool = False,
) -> dict[str, Any]:
    """Download a published skill or plugin zip and install into AppData.

    Free items stay anonymous. Paid items require a signed-in DuckyOS account
    with a recorded purchase (server-enforced).

    Pass ``is_update=True`` when the pack is already installed locally so the
    Store does not increment ``install_count``.
    """
    with _STORE_INSTALL_LOCK:
        return _store_download_and_install_unlocked(
            slug,
            version=version,
            replace=replace,
            paid=paid,
            is_update=is_update,
        )


def _store_download_and_install_unlocked(
    slug: str,
    *,
    version: str | None = None,
    replace: bool = True,
    paid: bool | None = None,
    is_update: bool = False,
) -> dict[str, Any]:
    import base64
    import hashlib
    import io
    import zipfile

    slug = (slug or "").strip()
    if not slug:
        raise DuckyOSAccountError("slug required", code="bad_request")
    body: dict[str, Any] = {"slug": slug, "isUpdate": bool(is_update)}
    if version:
        body["version"] = str(version).strip()
    # Prefer authenticated download when logged in (needed for paid; fine for free).
    blob = _load_blob()
    logged_in = bool(blob.get("device_key") or blob.get("session_value"))
    allow_anon = not logged_in and not paid
    try:
        payload = _store_collect(
            "download",
            body,
            allow_anonymous=allow_anon,
            timeout=120.0,
        )
    except DuckyOSAccountError as exc:
        msg = (exc.message or "").lower()
        if "purchase_required" in msg or exc.code in ("store_error", "forbidden"):
            raise DuckyOSAccountError(
                exc.message or "Purchase required",
                code="purchase_required",
            ) from exc
        raise
    zip_b64 = str(payload.get("zipB64") or "")
    if not zip_b64:
        raise DuckyOSAccountError("Store download returned no zip", code="store_empty")
    try:
        raw = base64.b64decode(zip_b64)
    except Exception as exc:
        raise DuckyOSAccountError(f"Invalid zip data: {exc}", code="store_bad_zip") from exc
    expected = str(payload.get("sha256") or "").strip().lower()
    if not expected:
        raise DuckyOSAccountError("Store download missing sha256", code="store_hash_missing")
    got = hashlib.sha256(raw).hexdigest()
    if got != expected:
        raise DuckyOSAccountError("Downloaded zip failed hash check", code="store_hash_mismatch")

    is_plugin = False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            is_plugin = any(Path(n).name == "plugin.json" for n in zf.namelist())
    except zipfile.BadZipFile as exc:
        raise DuckyOSAccountError(f"Invalid zip: {exc}", code="store_bad_zip") from exc

    if is_plugin:
        from backend.uefn_plugins.store import import_plugin_from_bytes

        result = import_plugin_from_bytes(raw, source="store", replace=bool(replace))
    else:
        from backend.skills.store import import_skill_pack_from_bytes

        result = import_skill_pack_from_bytes(
            raw,
            pack_id=slug,
            replace=bool(replace),
            source="store",
            store_slug=slug,
            store_version=str(payload.get("version") or "").strip() or None,
        )
        if result.get("ok"):
            try:
                from frontend.skill_deploy import sync_skill_all_ides
                from backend.agent.prompt import clear_skill_cache
                from frontend.settings import PanelSettings

                clear_skill_cache()
                sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
            except Exception:
                pass
    return {
        "ok": bool(result.get("ok")),
        "error": result.get("error"),
        "slug": slug,
        "kind": "plugin" if is_plugin else "skill",
        "version": str(payload.get("version") or ""),
        "pack_version": payload.get("pack_version"),
        "import": result,
    }
