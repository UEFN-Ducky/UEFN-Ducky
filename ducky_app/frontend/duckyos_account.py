"""DuckyOS tenant account login for the UEFN Ducky desktop app.

Starts a device-code grant against the tenant (default ``https://uefnducky.org``).
The Account tab shows a short code; the signed-in site ``/ducky`` page approves
it and the app polls for a ``dky_v1_`` device key (scope ``uefn-ducky.app``).
The token never goes in a URL. Credentials are stored DPAPI-encrypted in
``credentials.dat``. Passwords never enter the app.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import platform
import re
import secrets as secrets_mod
import select
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.response
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
        raise DuckyOSAccountError("Account URL must use https://", code="invalid_url")
    if not parsed.netloc:
        raise DuckyOSAccountError("Account URL is missing a host", code="invalid_url")
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
    # Fresh per call: the MCP bridge process must follow an account switch made in the app.
    from backend.agent.secrets import get_key_fresh

    raw = get_key_fresh(_CREDENTIALS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# (db path, login_version, account key) of the stored login. Every repo call asks who is
# signed in; the login row bumps meta.login_version on each write (any process), so one
# tiny meta read replaces the secrets read + json parse + DPAPI check.
_KEY_CACHE: tuple[str, str, str] | None = None


def account_key(blob: dict[str, Any] | None = None) -> str:
    """Who this PC is signed in as (``""`` signed out). Keys account-scoped caches."""
    global _KEY_CACHE
    if blob is not None:
        return _key_of(blob)
    from backend.agent.secrets import login_stamp

    # Stamp first: a login written between the stamp and the read is seen on the next call.
    stamp = login_stamp()
    hit = _KEY_CACHE
    if stamp is not None and hit is not None and hit[:2] == stamp:
        return hit[2]
    key = _key_of(_load_blob())
    _KEY_CACHE = None if stamp is None else (*stamp, key)
    return key


def forget_account_key() -> None:
    global _KEY_CACHE
    _KEY_CACHE = None


def _key_of(b: dict[str, Any]) -> str:
    if not (b.get("device_key") or b.get("session_value")):
        return ""
    who = str(b.get("email") or b.get("user_id") or "").strip().lower()
    return f"{str(b.get('base_url') or '').rstrip('/')}|{who}"


def _note_login_change(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Login / logout / expiry / unpair all land here (the one login funnel).

    The account data key cache (wrapped with the old device token) goes with the
    old login (plan §13); a new sign-in fetches the new account's key in the
    background. The panel drops account-scoped caches (Store catalog, prefs) and
    swaps chats and duckies, so one account never sees another's."""
    forget_account_key()
    try:
        from backend.uefn_plugins.scopes import forget_claims

        forget_claims()
    except Exception:
        pass
    switched = account_key(after) != account_key(before)
    try:
        from backend.uefn_plugins.data_crypto import on_login_change, prefetch

        on_login_change(before, after)
        if switched:
            prefetch()
    except Exception:
        pass
    if account_key(before) == account_key(after):
        return
    _tell_account_listeners(account_key(before), account_key(after))
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "duckyos_account_changed", "account": account_key(after)})
    except Exception:
        pass
    try:
        from backend.store import account_rows
        from backend.uefn_plugins.scopes import account_id_for
        from frontend.ui_web.agent_modes import notify_chats_changed

        # Chats are the signed-in account's: every window drops the old account's rows at
        # once and loads the new one's (the Duckies tab reloads on the account event).
        convs, folders = account_rows.owned_ids(account_id_for(account_key(before)))
        notify_chats_changed(open_tab=False, removed_conv_ids=convs, removed_folder_ids=folders)
    except Exception:
        pass
    try:
        from backend.uefn_plugins.scopes import account_id_for
        from backend.uefn_plugins.spaces import on_account_change

        on_account_change(account_id_for(account_key(before)), account_id_for(account_key(after)))
    except Exception:
        pass


def _save_blob(data: dict[str, Any]) -> None:
    from backend.agent.secrets import clear_key, set_key

    before = _load_blob()
    cleaned = {k: v for k, v in data.items() if v not in (None, "", [], {})}
    if cleaned:
        set_key(_CREDENTIALS_KEY, json.dumps(cleaned, separators=(",", ":")))
    else:
        clear_key(_CREDENTIALS_KEY)
    _note_login_change(before, cleaned)


def _clear_blob() -> None:
    from backend.agent.secrets import clear_key

    before = _load_blob()
    clear_key(_CREDENTIALS_KEY)
    _note_login_change(before, {})


def pkce_pair() -> tuple[str, str]:
    """RFC 7636 S256: ``(code_verifier, code_challenge)``."""
    verifier = secrets_mod.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _clear_expired_auth() -> None:
    """Website session 401 with no device key — drop local credentials."""
    try:
        stop_presence_heartbeat()
    except Exception:
        pass
    _clear_blob()


def _drop_website_session() -> None:
    """Cookie 401 while this PC is still paired. Keep the device key."""
    blob = _load_blob()
    for key in (
        "session_value",
        "session_name",
        "csrf_name",
        "csrf_value",
        "challenge_name",
        "challenge_value",
    ):
        blob.pop(key, None)
    _save_blob(blob)


def _unpair_this_pc() -> None:
    """Drop this PC's device key; keep the website session if present."""
    blob = _load_blob()
    blob.pop("device_key", None)
    blob.pop("device_key_id", None)
    blob["device_key_error"] = ""
    _save_blob(blob)
    stop_presence_heartbeat()
    stop_rpc_waiter()
    try:
        publish_device_presence(live=False)
    except Exception:
        pass


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
        f"{base}/api/v1/auth/api-keys",
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
    if not key_id:
        return
    base = str(blob.get("base_url") or "").rstrip("/")
    if base and blob.get("session_value"):
        try:
            _request(
                "DELETE",
                f"{base}/api/v1/auth/api-keys/{key_id}",
                cookie_header=_session_cookie_header(blob),
                csrf_token=str(blob.get("csrf_value") or ""),
                timeout=12.0,
            )
        except DuckyOSAccountError:
            pass
    try:
        _plugin_collect(
            "uefn-ducky",
            "desktop-device-revoke",
            {"keyId": key_id},
            unavailable_code="rpc_unavailable",
            unavailable_msg="Remote mailbox plugin is not active on this tenant yet.",
            error_code="revoke_failed",
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
        f"{base}/api/v1/auth/me",
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
        f"{base}/api/v1/acl/self",
        cookie_header=_session_cookie_header(blob),
    )
    _apply_session_cookies(blob, cookies)
    if status == 200 and isinstance(payload, dict):
        perms = payload.get("permissions")
        if isinstance(perms, list):
            blob["permissions"] = [str(p) for p in perms]
    return blob


def _enable_remote_access() -> None:
    """Browser access on, before the first presence ping after a code login."""
    try:
        from frontend.settings import PanelSettings
        from frontend.ui_web.panel_api import _save_panel_settings

        settings = PanelSettings.load()
        if getattr(settings, "remote_access", False):
            return
        settings.remote_access = True
        _save_panel_settings(settings)
    except Exception:
        pass


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
_DEVICE_LOGIN: dict[str, Any] = {}


def cancel_browser_login() -> dict[str, Any]:
    """Signal a waiting ``start_browser_login`` to abort."""
    _BROWSER_LOGIN_CANCEL.set()
    _DEVICE_LOGIN.clear()
    return get_status()


def start_browser_login(base_url: str = "", *, timeout_secs: float = 600.0) -> dict[str, Any]:
    """Show a device code in the app; poll until the site approves it."""
    base = normalize_base_url(base_url or resolve_base_url())
    _persist_base_url(base)

    if not _BROWSER_LOGIN_LOCK.acquire(blocking=False):
        # Deep link + Account tab both start login; show the live code instead of locking the UI.
        status = get_status()
        status["ok"] = True
        status["browser_pending"] = True
        return status

    _BROWSER_LOGIN_CANCEL.clear()
    _DEVICE_LOGIN.clear()
    try:
        started = _plugin_collect(
            "uefn-ducky",
            "desktop-device-start",
            {},
            unavailable_code="auth_unavailable",
            unavailable_msg="Desktop login plugin is not active on this tenant yet.",
            error_code="device_start_failed",
            allow_anonymous=True,
            timeout=20.0,
        )
        user_code = str(started.get("user_code") or "").strip()
        device_code = str(started.get("device_code") or "").strip()
        if not user_code or not device_code:
            raise DuckyOSAccountError("Could not start login.", code="device_start_failed")
        _DEVICE_LOGIN.update({"user_code": user_code, "device_code": device_code})

        deadline = __import__("time").monotonic() + max(30.0, float(timeout_secs))
        token = ""
        key_id = ""
        email = ""
        while __import__("time").monotonic() < deadline:
            if _BROWSER_LOGIN_CANCEL.is_set():
                raise DuckyOSAccountError("Browser login cancelled", code="cancelled")
            payload = _plugin_collect(
                "uefn-ducky",
                "desktop-device-poll",
                {"device_code": device_code},
                unavailable_code="auth_unavailable",
                unavailable_msg="Desktop login plugin is not active on this tenant yet.",
                error_code="device_poll_failed",
                allow_anonymous=True,
                timeout=20.0,
            )
            if str(payload.get("status") or "") == "pending":
                __import__("time").sleep(1.0)
                continue
            token = str(payload.get("token") or "")
            key_id = str(payload.get("keyId") or payload.get("key_id") or "")
            email = str(payload.get("email") or "")
            break

        if _BROWSER_LOGIN_CANCEL.is_set():
            raise DuckyOSAccountError("Browser login cancelled", code="cancelled")
        if not token.startswith("dky_v1_"):
            raise DuckyOSAccountError(
                "Timed out waiting for the code to be approved.",
                code="browser_timeout",
            )
        blob: dict[str, Any] = {
            "base_url": base,
            "email": email,
            "device_key": token,
            "device_key_id": key_id,
            "device_key_error": "",
        }
        _save_blob(blob)
        # The presence thread stamps the website card from remote_access.
        # Turn it on before the first ping, or the card goes yellow
        # ("Disabled from UEFN Ducky side") and stays that way.
        _enable_remote_access()
        start_presence_heartbeat()
        start_rpc_waiter()

        def _after_login() -> None:
            try:
                fetch_agent_caps(_load_blob())
                publish_agent_catalog()
            except Exception:
                pass

        # ponytail: caps can take 10s+; Account is already signed in.
        __import__("threading").Thread(
            target=_after_login, daemon=True, name="ducky-login-followup"
        ).start()
        status = get_status()
        status["ok"] = True
        return status
    finally:
        _DEVICE_LOGIN.clear()
        _BROWSER_LOGIN_LOCK.release()


def logout() -> dict[str, Any]:
    _BROWSER_LOGIN_CANCEL.set()
    stop_presence_heartbeat()
    stop_rpc_waiter()
    blob = _load_blob()
    base = str(blob.get("base_url") or "").rstrip("/")
    # End browser access before the device key goes.
    try:
        from frontend.ui_web.panel_httpd import kick_all_remote

        kick_all_remote()
    except Exception:
        pass
    _revoke_device_key(blob)
    if base and blob.get("session_value"):
        try:
            _request(
                "POST",
                f"{base}/api/v1/auth/logout",
                cookie_header=_session_cookie_header(blob),
                csrf_token=str(blob.get("csrf_value") or ""),
                timeout=10.0,
            )
        except DuckyOSAccountError:
            pass
    _clear_blob()
    return get_status()


def list_account_pcs() -> dict[str, Any]:
    """PCs linked to this Ducky account (website + this panel)."""
    blob = _load_blob()
    mine = str(blob.get("device_key_id") or "").strip()
    row = _plugin_collect(
        "uefn-ducky",
        "desktop-devices",
        {},
        unavailable_code="rpc_unavailable",
        unavailable_msg="Can't load PCs right now.",
        error_code="devices_failed",
    )
    devices = []
    raw = row.get("devices") if isinstance(row, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key_id = str(item.get("keyId") or item.get("key_id") or "").strip()
            devices.append(
                {
                    "keyId": key_id,
                    "name": str(item.get("name") or "UEFN Ducky"),
                    "live": bool(item.get("live")),
                    "last_seen": int(item.get("last_seen") or 0),
                    "mine": bool(mine and key_id == mine),
                }
            )
    return {"ok": True, "devices": devices}


def revoke_account_pc(key_id: str = "") -> dict[str, Any]:
    """Drop one linked PC. If it is this panel, clear the local device key too."""
    key_id = str(key_id or "").strip()
    if not key_id:
        raise DuckyOSAccountError("No PC selected", code="no_key")
    blob = _load_blob()
    mine = str(blob.get("device_key_id") or "").strip()
    base = str(blob.get("base_url") or "").rstrip("/")
    if base and blob.get("session_value"):
        try:
            _request(
                "DELETE",
                f"{base}/api/v1/auth/api-keys/{key_id}",
                cookie_header=_session_cookie_header(blob),
                csrf_token=str(blob.get("csrf_value") or ""),
                timeout=12.0,
            )
        except DuckyOSAccountError:
            pass
    _plugin_collect(
        "uefn-ducky",
        "desktop-device-revoke",
        {"keyId": key_id},
        unavailable_code="rpc_unavailable",
        unavailable_msg="Can't remove that PC right now.",
        error_code="revoke_failed",
    )
    if mine and key_id == mine:
        _unpair_this_pc()
    return {"ok": True, **get_status()}


def get_status() -> dict[str, Any]:
    """Public status for the UI — never includes session/device secrets."""
    blob = _load_blob()
    base = str(blob.get("base_url") or resolve_base_url())
    device_active = bool(blob.get("device_key"))
    session_ok = bool(blob.get("session_value"))
    logged_in = device_active or session_ok
    user_code = str(_DEVICE_LOGIN.get("user_code") or "")
    return {
        "logged_in": logged_in,
        "needs_code": False,
        "browser_pending": bool(user_code),
        "user_code": user_code,
        "base_url": base,
        "default_base_url": DEFAULT_BASE_URL,
        "email": str(blob.get("email") or "") if logged_in else "",
        "display_name": str(blob.get("display_name") or "") if logged_in else "",
        "user_id": str(blob.get("user_id") or "") if logged_in else "",
        # Same key the Store catalog cache and plugin data use ("" signed out).
        "account": account_key(blob),
        "roles": list(blob.get("roles") or []) if logged_in else [],
        "permissions": list(blob.get("permissions") or []) if logged_in else [],
        "device_key_active": device_active if logged_in else False,
        "device_key_error": str(blob.get("device_key_error") or "") if logged_in else "",
        "auth_mode": "browser",
        "agent_denied": list(blob.get("agent_denied") or []) if logged_in else [],
        "agent_settings": dict(blob.get("agent_settings") or {}) if logged_in else {},
    }


_DENY_MAX = 2000
_DENY_NAME_MAX = 128


def _name_ok(name: str) -> bool:
    return bool(name) and len(name) <= _DENY_NAME_MAX and all(
        ch.isalnum() or ch in "_-.:" for ch in name
    )


def sanitize_denied_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip() if isinstance(item, str) else ""
        if not _name_ok(name) or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= _DENY_MAX:
            break
    return out


def cloud_denied_names() -> set[str]:
    blob = _load_blob()
    return {
        str(name)
        for name in (blob.get("agent_denied") or [])
        if isinstance(name, str) and name.strip()
    }


_CAP_DEFAULTS: dict[str, bool] = {
    "allow_settings_write": True,
    "allow_agent_clicks": False,
    "allow_see_uefn": True,
    "allow_see_other_programs": True,
}
_UEFN_SEE_TOOLS = frozenset({"take_high_res_screenshot", "get_viewport_camera"})
_OTHER_APP_PREFIXES = ("blender_", "unity_", "roblox_", "godot_")
_OTHER_APP_TOOLS = frozenset({"snip_screen"})


def _cap_bools(raw: dict[str, Any] | None) -> dict[str, bool]:
    src = raw if isinstance(raw, dict) else {}
    return {key: bool(src.get(key, default)) for key, default in _CAP_DEFAULTS.items()}


def cloud_settings_ceiling() -> dict[str, bool] | None:
    blob = _load_blob()
    raw = blob.get("agent_settings")
    if not isinstance(raw, dict) or not raw:
        return None
    return _cap_bools(raw)


def effective_allow_settings_write(local: bool) -> bool:
    return bool(local)


def effective_allow_agent_clicks(local: bool) -> bool:
    return bool(local)


def tool_blocked_by_caps(name: str, settings: dict[str, bool] | None = None) -> str | None:
    """Why this tool is hidden, or None if allowed. ``settings``: one _panel_cap_settings()
    read for a whole tool list (it was read again for every tool)."""
    settings = _panel_cap_settings() if settings is None else settings
    if not settings.get("allow_see_uefn", True) and name in _UEFN_SEE_TOOLS:
        return "See UEFN is off — re-enable in Settings → Account"
    if not settings.get("allow_see_other_programs", True) and (
        name in _OTHER_APP_TOOLS or name.startswith(_OTHER_APP_PREFIXES)
    ):
        return "See other programs is off — re-enable in Settings → Account"
    return None


def _panel_cap_settings() -> dict[str, bool]:
    try:
        from frontend.settings import PanelSettings

        s = PanelSettings.load()
        return {key: bool(getattr(s, key, default)) for key, default in _CAP_DEFAULTS.items()}
    except Exception:
        raw = _load_blob().get("agent_settings")
        return _cap_bools(raw if isinstance(raw, dict) else {})


def _apply_caps_to_panel(settings: dict[str, Any]) -> None:
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    for key in _CAP_DEFAULTS:
        if key in settings:
            setattr(s, key, bool(settings[key]))
    s.save()


def agent_caps_status(*, include_catalog: bool = False) -> dict[str, Any]:
    blob = _load_blob()
    denied = [str(n) for n in (blob.get("agent_denied") or []) if isinstance(n, str)]
    settings = _panel_cap_settings()
    out: dict[str, Any] = {
        "ok": True,
        "denied": denied,
        "settings": settings,
        "denied_count": len(denied),
    }
    if include_catalog:
        try:
            from frontend.ui_web.mcp_catalog import build_caps_catalog

            catalog = build_caps_catalog()
        except Exception:
            catalog = {"categories": []}
        out["catalog"] = catalog if isinstance(catalog, dict) else {"categories": []}
    return out


def set_agent_caps(denied: Any = None, settings: Any = None) -> dict[str, Any]:
    """Desktop is the limiter. Cloud is a replica when this PC is signed in."""
    blob = _load_blob()
    if denied is not None:
        blob["agent_denied"] = sanitize_denied_names(denied)
    cur = blob.get("agent_settings") if isinstance(blob.get("agent_settings"), dict) else {}
    next_settings = _cap_bools(cur)
    next_settings.update(_panel_cap_settings())
    if isinstance(settings, dict):
        for key in _CAP_DEFAULTS:
            if key in settings:
                next_settings[key] = bool(settings[key])
    blob["agent_settings"] = next_settings
    blob["agent_caps_local"] = True
    _save_blob(blob)
    try:
        _apply_caps_to_panel(next_settings)
    except Exception:
        pass
    _push_agent_caps(blob)
    return agent_caps_status(include_catalog=False)


def _push_agent_caps(blob: dict[str, Any]) -> None:
    if not blob.get("device_key"):
        return
    try:
        _plugin_collect(
            "uefn-ducky",
            "agent-caps-set",
            {
                "denied": list(blob.get("agent_denied") or []),
                "settings": dict(blob.get("agent_settings") or {}),
            },
            unavailable_code="auth_unavailable",
            unavailable_msg="Desktop login plugin is not active on this tenant yet.",
            error_code="agent_caps_failed",
            timeout=12.0,
        )
    except Exception:
        return


def _store_agent_caps(blob: dict[str, Any], row: dict[str, Any] | None) -> None:
    if not isinstance(row, dict):
        return
    denied = row.get("denied")
    if isinstance(denied, list):
        blob["agent_denied"] = [str(n) for n in denied if isinstance(n, str) and n.strip()]
    settings = row.get("settings")
    if isinstance(settings, dict):
        blob["agent_settings"] = _cap_bools(settings)
    _save_blob(blob)


def fetch_agent_caps(blob: dict[str, Any] | None = None) -> dict[str, Any]:
    blob = blob if blob is not None else _load_blob()
    if blob.get("agent_caps_local"):
        return blob
    if not (blob.get("device_key") or blob.get("session_value")):
        return blob
    try:
        row = _plugin_collect(
            "uefn-ducky",
            "agent-caps-get",
            {},
            unavailable_code="auth_unavailable",
            unavailable_msg="Desktop login plugin is not active on this tenant yet.",
            error_code="agent_caps_failed",
            timeout=12.0,
        )
    except Exception:
        return blob
    _store_agent_caps(blob, row)
    try:
        raw = blob.get("agent_settings")
        if isinstance(raw, dict):
            _apply_caps_to_panel(raw)
    except Exception:
        pass
    return blob


def publish_agent_catalog() -> None:
    """Push the installed-tool catalog once (login / plugin toggle). Not a poll."""
    blob = _load_blob()
    if not blob.get("device_key"):
        return
    try:
        from frontend.ui_web.mcp_catalog import build_caps_catalog

        catalog = build_caps_catalog()
    except Exception:
        return
    if not isinstance(catalog, dict) or not catalog.get("categories"):
        return
    try:
        _plugin_collect(
            "uefn-ducky",
            "agent-caps-catalog",
            {"catalog": catalog, "appVersion": __version__},
            unavailable_code="auth_unavailable",
            unavailable_msg="Desktop login plugin is not active on this tenant yet.",
            error_code="agent_caps_failed",
            timeout=20.0,
        )
    except Exception:
        return


def refresh_status() -> dict[str, Any]:
    """Refresh identity when a session cookie exists; device-key-only stays as-is.
    Plugins that keep per-account answers ask again too, so a role granted since sign-in shows."""
    blob = _load_blob()
    _tell_account_listeners(account_key(blob), account_key(blob))
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


# Plugins that keep something per account (an access answer, a cache) hear every sign-in,
# sign-out and Account → Refresh here: fn(before_account_key, after_account_key); the same
# key twice means "this account, ask again".
_ACCOUNT_LISTENERS: list[Any] = []


def add_account_listener(fn: Any) -> None:
    if callable(fn) and fn not in _ACCOUNT_LISTENERS:
        _ACCOUNT_LISTENERS.append(fn)


def _tell_account_listeners(before: str, after: str) -> None:
    for fn in list(_ACCOUNT_LISTENERS):
        try:
            fn(before, after)
        except Exception:
            pass


# A parked connection idle longer than this is closed, not reused: a router may
# have dropped it without telling us, and a request on it would hang to timeout.
_KEEPALIVE_IDLE_S = 30.0
_KEEPALIVE_MAX_PARKED = 4


def _still_open(conn: http.client.HTTPConnection) -> bool:
    """An idle keep-alive socket has nothing to read; readable means the site closed it."""
    sock = conn.sock
    if sock is None:
        return False
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable


class _KeepAlive:
    """Reuse site connections instead of a new TCP + TLS handshake per request.

    urllib sends ``Connection: close`` and drops the socket after every call.
    Remote access asks the site's mailbox every few seconds all day, so that was up
    to 1,500 handshakes an hour for empty answers. Redirects, proxies and HTTP
    errors still go through urllib's own handlers.
    """

    def _keepalive_init(self) -> None:
        self._parked: dict[tuple[type, str], list[tuple[float, http.client.HTTPConnection]]] = {}
        self._parked_lock = threading.Lock()

    def _take(self, key: tuple[type, str]) -> http.client.HTTPConnection | None:
        now = time.monotonic()
        with self._parked_lock:
            parked = self._parked.get(key) or []
            while parked:
                since, conn = parked.pop()
                if now - since < _KEEPALIVE_IDLE_S and _still_open(conn):
                    return conn
                conn.close()
        return None

    def _park(self, key: tuple[type, str], conn: http.client.HTTPConnection) -> None:
        with self._parked_lock:
            parked = self._parked.setdefault(key, [])
            if len(parked) < _KEEPALIVE_MAX_PARKED:
                parked.append((time.monotonic(), conn))
                return
        conn.close()

    def _keepalive_open(self, http_class: type, req: urllib.request.Request, **conn_args: Any) -> Any:
        if req._tunnel_host or not req.host:  # proxy CONNECT: urllib's own path
            return self.do_open(http_class, req, **conn_args)  # type: ignore[attr-defined]
        key = (http_class, req.host)
        headers = dict(req.unredirected_hdrs)
        headers.update({k: v for k, v in req.headers.items() if k not in headers})
        headers = {name.title(): val for name, val in headers.items()}
        timeout = req.timeout if isinstance(req.timeout, (int, float)) else socket.getdefaulttimeout()
        for fresh in (False, True):
            conn = None if fresh else self._take(key)
            reused = conn is not None
            if conn is None:
                conn = http_class(req.host, timeout=req.timeout, **conn_args)
            else:
                conn.timeout = timeout
                if conn.sock is not None:
                    conn.sock.settimeout(timeout)
            try:
                conn.request(
                    req.get_method(), req.selector, req.data, headers,
                    encode_chunked=req.has_header("Transfer-encoding"),
                )
                resp = conn.getresponse()
                body = resp.read()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as err:
                conn.close()
                if reused:
                    continue  # the site closed a parked connection: ask once on a new one
                raise urllib.error.URLError(err) from err
            except OSError as err:
                conn.close()
                raise urllib.error.URLError(err) from err
            except BaseException:
                conn.close()
                raise
            if resp.will_close:
                conn.close()
            else:
                self._park(key, conn)
            out = urllib.response.addinfourl(io.BytesIO(body), resp.headers, req.get_full_url(), resp.status)
            out.msg = resp.reason  # type: ignore[attr-defined]
            return out
        raise urllib.error.URLError("connection closed")


class _KeepAliveHTTPHandler(_KeepAlive, urllib.request.HTTPHandler):
    def __init__(self) -> None:
        super().__init__()
        self._keepalive_init()

    def http_open(self, req: urllib.request.Request) -> Any:
        return self._keepalive_open(http.client.HTTPConnection, req)


class _KeepAliveHTTPSHandler(_KeepAlive, urllib.request.HTTPSHandler):
    def __init__(self) -> None:
        super().__init__()
        self._keepalive_init()

    def https_open(self, req: urllib.request.Request) -> Any:
        return self._keepalive_open(http.client.HTTPSConnection, req, context=self._context)


_SITE_OPENER: urllib.request.OpenerDirector | None = None


def _site_urlopen(req: urllib.request.Request, *, timeout: float) -> Any:
    global _SITE_OPENER
    if _SITE_OPENER is None:
        _SITE_OPENER = urllib.request.build_opener(_KeepAliveHTTPHandler(), _KeepAliveHTTPSHandler())
    return _SITE_OPENER.open(req, timeout=timeout)


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
        with _site_urlopen(req, timeout=timeout) as resp:
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
            used_bearer = bool(extra_headers.get("Authorization"))
            # A paired PC's device key is only valid on the desktop plugin.
            # /auth/me, Store, and Stripe answer 401 because they want a
            # website cookie. That is not "this PC was disconnected".
            if used_bearer and blob.get("device_key"):
                if "/api/v1/plugins/uefn-ducky/collect/" in path:
                    _unpair_this_pc()
                    raise DuckyOSAccountError(
                        "This PC was disconnected", code="device_unpaired"
                    ) from exc
                raise DuckyOSAccountError(
                    "That page needs a website sign-in. This PC stays connected.",
                    code="session_required",
                ) from exc
            if blob.get("device_key"):
                _drop_website_session()
            else:
                _clear_expired_auth()
            raise DuckyOSAccountError(
                "Session expired — log in again", code="session_expired"
            ) from exc
        return int(exc.code), parsed, raw
    except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException) as exc:
        # HTTPException: a body cut off mid-read (IncompleteRead), a bad status line.
        raise DuckyOSAccountError(f"Network error: {exc}", code="network") from exc


_PRESENCE_STOP = __import__("threading").Event()
_PRESENCE_THREAD: Any = None
_PRESENCE_LOCK = __import__("threading").Lock()
_PRESENCE_INTERVAL_S = 90.0
# While nothing it reports has changed, a PC reports less often, up to this,
# and never past what the site's plugins say still counts as online
# (``liveSeconds``); a site that doesn't say keeps the 90 s beat.
_PRESENCE_QUIET_MAX_S = 300.0
_PRESENCE_LIVE_S: dict[str, float] = {}
_PRESENCE_LAST: dict[str, Any] = {"fingerprint": None, "at": 0.0}
_RPC_STOP = __import__("threading").Event()
_RPC_THREAD: Any = None
_RPC_LOCK = __import__("threading").Lock()
# Ends a pause between mailbox waits at once (stop).
_RPC_WAKE = threading.Event()
# Remote access waits on the site's desktop-rpc channel: the site holds each wait up
# to 25 s and answers the moment a phone or the website sends this account a command,
# so the PC asks the mailbox only when told, not every second all day.
_RPC_CHANNEL = "/api/v1/plugins/uefn-ducky/channels/desktop-rpc"
RPC_ALLOWLIST = frozenset(
    {
        "list_folders",
        "list_conversations",
        "list_all_conversations",
        "load_messages",
        "create_conversation",
        "rename_conversation",
        "move_conversation",
        "delete_conversation",
        "send_message",
        "cancel_agent",
        "group_create",
        "group_add_member",
        "group_set_leader",
        "group_invite",
        "group_members",
        "list_changesets",
        "get_changeset",
        "remote_snapshot",
        "rtc_connect",
        "rtc_report",
        # Your other UEFN Duckies pull these to sync (frontend.ducky_sync).
        "ducky_sync_duckies",
        "ducky_sync_chats",
        "ducky_sync_rows",
    }
)

REMOTE_DENY = frozenset(
    {
        # Only this PC's own window says whether Ducky is in use, and only it updates Ducky.
        "report_update_ui",
        "apply_auto_update_now",
        "set_window_bounds",
        "get_window_bounds",
        "minimize_window",
        "hide_window",
        "toggle_maximize",
        "is_window_maximized",
        "begin_native_window_move",
        "begin_native_window_resize",
        "uses_native_window_chrome",
        "pick_project_path",
        "inspect_project_folder",
        "open_focus_window",
        "open_focus_window_group",
        "open_focus_window_at_point",
        "adopt_tab_into_this_focus_window",
        "raise_focus_window",
        "browser_pane_open",
        "browser_pane_set_bounds",
        "browser_pane_navigate",
        "browser_pane_command",
        "browser_pane_state",
        "browser_pane_close",
        "browser_pane_list",
        "browser_pane_hide_all",
        "browser_pane_snapshot",
        "browser_clear_browsing_data",
        "open_devtools",
        "copy_text",
        "get_mcp_config",
        "set_mcp_config",
        "set_uefn_plugin_secret",
        "set_uefn_plugin_allowed_models",
        "test_uefn_plugin_secret",
        "test_key",
        "rtc_signal",
        "window_input",
        "window_box",
        "capture_list_shield",
        "snip_screen",
        # Windows speech listens on the PC's mic — a phone must use its own browser speech.
        "voice_win_stt_prewarm",
        "voice_win_stt_start",
        "voice_win_stt_poll",
        "voice_win_stt_stop",
        "voice_win_stt_cancel",
        "voice_open_windows_settings",
    }
)

# verse-lsp talks over a 127.0.0.1 WebSocket that a phone or the website cannot reach,
# so starting one for them only leaves a language server running on the PC for nobody.
# Every remote page also brings a new window id, and enough of those push the Ducky
# window's own session out of the pool. A browser on this PC can reach the socket.
OFF_PC_DENY = frozenset(
    {
        "get_verse_lsp_status",
        "start_verse_lsp",
        "stop_verse_lsp",
        "reload_uefn_plugin",
        # Reaching your other UEFN Duckies is for this PC's own windows: a phone or
        # the website must never use this Ducky as a relay to the others.
        "ducky_link_list",
        "ducky_link_call",
        "ducky_link_rtc_connect",
        "ducky_link_open",
        "ducky_sync_list",
        "ducky_sync_copy",
        "ducky_sync_now",
        "ducky_sync_forget",
        # Live editing's peer connections live in this PC's window: nothing outside it
        # may answer for them or feed them messages.
        "rtc_reply",
        "rtc_event",
        # The right-click menu over typed text answers this PC's own WebView2 menu.
        "native_menu_pick",
        # Sandboxes start programs on this PC and self-care stops them: never from a phone
        # or the website.
        "sandbox_call",
        "sandbox_status",
        "sandbox_stop_program",
        "sandbox_delete",
        "sandbox_open_folder",
        "sandbox_apps",
        "sandbox_relaunch",
        "ducky_health_snapshot",
        "ducky_self_care_run",
        # Links to files on this PC are for this PC's windows (they point at 127.0.0.1).
        "chat_tool_view",
    }
)


def remote_denied(method: str, *, on_this_pc: bool = False) -> bool:
    """True when a panel call from outside the Ducky window must be refused."""
    return method in REMOTE_DENY or (not on_this_pc and method in OFF_PC_DENY)


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


def _remote_direct_report() -> dict[str, Any]:
    try:
        from frontend.remote_direct import last_report

        return last_report()
    except Exception:
        return {}


def _note_live_seconds(which: str, answer: Any) -> None:
    """Remember how long the site counts this PC online after a report; an older plugin doesn't say."""
    payload = answer.get("payload") if isinstance(answer, dict) else None
    source = payload if isinstance(payload, dict) else answer
    live = source.get("liveSeconds") if isinstance(source, dict) else None
    if isinstance(live, (int, float)) and not isinstance(live, bool) and live > 0:
        _PRESENCE_LIVE_S[which] = float(live)
    else:
        _PRESENCE_LIVE_S.pop(which, None)


def _presence_quiet_s() -> float:
    """How long a PC whose report hasn't changed may wait before the next one."""
    if not {"store", "device"} <= _PRESENCE_LIVE_S.keys():
        return _PRESENCE_INTERVAL_S
    window = min(_PRESENCE_LIVE_S["store"], _PRESENCE_LIVE_S["device"]) - 30.0
    return max(_PRESENCE_INTERVAL_S, min(_PRESENCE_QUIET_MAX_S, window))


def _presence_fingerprint(body: dict[str, Any]) -> str:
    keep = {k: body.get(k) for k in ("email", "projectLabel", "uefnOnline", "appVersion", "remoteDirect")}
    keep["remoteAccess"] = _remote_access_on()
    return json.dumps(keep, sort_keys=True, default=str)


def send_presence_heartbeat(*, only_if_changed: bool = False) -> bool:
    """POST this PC's presence to the account's Store hub. Returns False if not logged in / skipped.

    only_if_changed: skip it (True) while the report is unchanged and the site
    still counts the last one as online.
    """
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
        "direct": True,
        "directProtocol": 1,
        "remoteDirect": _remote_direct_report(),
    }
    try:
        from frontend import usage_stats

        if usage_stats.enabled():
            body["installId"] = usage_stats.install_id()
    except Exception:
        pass
    fingerprint = _presence_fingerprint(body)
    if (
        only_if_changed
        and fingerprint == _PRESENCE_LAST["fingerprint"]
        and time.monotonic() - _PRESENCE_LAST["at"] < _presence_quiet_s()
    ):
        return True
    try:
        status, parsed, _raw = api_request(
            "POST",
            "/api/v1/plugins/uefn-ducky-store/collect/presence",
            body,
            prefer_bearer=True,
            timeout=12.0,
        )
        ok = 200 <= int(status) < 300
    except DuckyOSAccountError:
        return False
    except Exception:
        return False
    _note_live_seconds("store", parsed if ok else None)
    publish_device_presence()
    if ok:
        _PRESENCE_LAST.update(fingerprint=fingerprint, at=time.monotonic())
    return ok


def _remote_access_on() -> bool:
    try:
        from frontend.settings import PanelSettings

        return bool(getattr(PanelSettings.load(), "remote_access", False))
    except Exception:
        return False


def publish_device_presence(*, live: bool | None = None) -> None:
    """Site PC badge: Live only while remote access is on. live=False clears last_seen."""
    blob = _load_blob()
    key_id = str(blob.get("device_key_id") or "").strip()
    if not key_id:
        return
    if live is None:
        live = _remote_access_on()
    body: dict[str, Any] = {"keyId": key_id}
    if not live:
        body["live"] = False
    try:
        answer = _plugin_collect(
            "uefn-ducky",
            "desktop-device-heartbeat",
            body,
            unavailable_code="auth_unavailable",
            unavailable_msg="Desktop login plugin is not active on this tenant yet.",
            error_code="device_heartbeat_failed",
            timeout=12.0,
        )
        _note_live_seconds("device", answer)
    except Exception:
        _note_live_seconds("device", None)


def start_presence_heartbeat() -> None:
    """Daemon thread: while logged in, report this PC's presence about once a minute."""
    global _PRESENCE_THREAD
    with _PRESENCE_LOCK:
        if _PRESENCE_THREAD is not None and _PRESENCE_THREAD.is_alive():
            return
        if not (_load_blob().get("device_key") or _load_blob().get("session_value")):
            return
        _PRESENCE_STOP.clear()

        def _loop() -> None:
            # A change goes out within 90 s; an unchanged PC reports less often.
            _PRESENCE_LAST.update(fingerprint=None, at=0.0)
            while not _PRESENCE_STOP.is_set():
                try:
                    send_presence_heartbeat(only_if_changed=True)
                except Exception:
                    pass
                _PRESENCE_STOP.wait(_PRESENCE_INTERVAL_S)

        _PRESENCE_THREAD = __import__("threading").Thread(
            target=_loop, daemon=True, name="duckyos-presence"
        )
        _PRESENCE_THREAD.start()
        start_rpc_waiter()
        # Chats and duckies from your other UEFN Duckies (peer to peer, sealed).
        from frontend import ducky_sync

        ducky_sync.start()


def stop_presence_heartbeat() -> None:
    _PRESENCE_STOP.set()
    stop_rpc_waiter()
    from frontend import ducky_sync

    ducky_sync.stop()


def call_panel_method(api: Any, name: str, args: Any = None) -> Any:
    """Map a JSON args object or positional list onto a PanelApi method."""
    import inspect
    import json as _json

    fn = getattr(api, name, None)
    if not callable(fn):
        raise AttributeError(name)
    sig = inspect.signature(fn)
    params = [
        p
        for p in sig.parameters.values()
        if p.name != "self"
        and p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    if isinstance(args, dict):
        kwargs: dict[str, Any] = {}
        for param in params:
            if param.name in args:
                kwargs[param.name] = args[param.name]
            elif param.default is inspect.Parameter.empty and param.kind != inspect.Parameter.KEYWORD_ONLY:
                raise TypeError(f"missing argument: {param.name}")
        result = fn(**kwargs)
    elif isinstance(args, (list, tuple)):
        result = fn(*args)
    else:
        result = fn()
    return _json.loads(_json.dumps(result, default=str))


def dispatch_desktop_rpc(method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one allowlisted PanelApi method for the website mailbox."""
    name = (method or "").strip()
    if name not in RPC_ALLOWLIST:
        return {"ok": False, "error": "method not allowed"}
    from frontend.ui_web.panel_api import shared_panel_api

    raw = args if isinstance(args, dict) else {}
    if name == "remote_snapshot":
        try:
            return {"ok": True, "result": _remote_snapshot()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if name == "rtc_connect":
        from frontend.remote_direct import rtc_connect

        try:
            out = rtc_connect(raw)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if out.get("error") and not out.get("answer"):
            return {"ok": False, "error": str(out["error"]), "result": out}
        return {"ok": True, "result": out}
    if name == "rtc_report":
        from frontend.remote_direct import note_report

        note_report(raw)
        return {"ok": True, "result": True}
    if name.startswith("ducky_sync_"):
        from frontend import ducky_sync

        try:
            if name == "ducky_sync_duckies":
                return {"ok": True, "result": ducky_sync.duckies_for_sync()}
            if name == "ducky_sync_chats":
                return {"ok": True, "result": ducky_sync.chats_for_sync()}
            return {"ok": True, "result": ducky_sync.rows_for_sync(str(raw.get("conv_id") or ""))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    try:
        result = call_panel_method(shared_panel_api(), name, raw)
    except TypeError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "result": result}


def _remote_snapshot() -> dict[str, Any]:
    """One disk read for the browser cache. Nothing is stored on the site."""
    import json as _json

    from frontend.ui_web.panel_api import shared_panel_api

    api = shared_panel_api()
    convs = list(api.list_all_conversations())
    messages: dict[str, Any] = {}
    groups: dict[str, Any] = {}
    for row in convs:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        if not cid:
            continue
        messages[cid] = api.load_messages(cid, full_results=True)
        if row.get("is_group"):
            groups[cid] = api.group_members(cid)
    ledger = list(api.list_changesets(limit=50))
    changesets: dict[str, Any] = {}
    for row in ledger:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("run_id") or "")
        if rid:
            changesets[rid] = api.get_changeset(rid)
    return _json.loads(
        _json.dumps(
            {
                "conversations": convs,
                "messages": messages,
                "groups": groups,
                "ledger": ledger,
                "changesets": changesets,
            },
            default=str,
        )
    )


def _poll_desktop_rpc_once() -> bool:
    """Read one mailbox request and answer it. True if a job was handled."""
    pending = _plugin_collect(
        "uefn-ducky",
        "desktop-rpc-wait",
        {"version": __version__, "direct": True, "protocol": 1},
        unavailable_code="rpc_unavailable",
        unavailable_msg="Remote mailbox plugin is not active on this tenant yet.",
        error_code="rpc_wait_failed",
        timeout=25.0,
    )
    if not pending.get("pending"):
        return False
    rid = str(pending.get("id") or "")
    method = str(pending.get("method") or "")
    args = pending.get("args")
    sender = str(pending.get("from") or "")
    if sender:
        # Another UEFN Ducky of the account asked: sealed both ways (frontend.ducky_link).
        from frontend.ducky_link import answer

        out = answer(rid, sender, method, args)
    else:
        out = dispatch_desktop_rpc(method, args if isinstance(args, dict) else {})
    body: dict[str, Any] = {"id": rid, "ok": bool(out.get("ok"))}
    if "result" in out:
        body["result"] = out["result"]
    if out.get("error"):
        body["error"] = out["error"]
    _plugin_collect(
        "uefn-ducky",
        "desktop-rpc-result",
        body,
        unavailable_code="rpc_unavailable",
        unavailable_msg="Remote mailbox plugin is not active on this tenant yet.",
        error_code="rpc_result_failed",
        timeout=15.0,
    )
    return True


def wait_desktop_rpc(cursor: int | None) -> tuple[bool, int | None]:
    """Hold one wait on the desktop-rpc channel. Returns (told, cursor): told when a
    command was sent to this account since ``cursor``; pass the cursor back next time."""
    path = _RPC_CHANNEL if cursor is None else f"{_RPC_CHANNEL}?after={int(cursor)}"
    status, parsed, _raw = api_request("GET", path, None, prefer_bearer=True, timeout=40.0)
    if not (200 <= int(status) < 300) or not isinstance(parsed, dict):
        raise DuckyOSAccountError("Can't connect right now. Try again.", code="rpc_wait_failed")
    nxt = parsed.get("cursor")
    messages = parsed.get("messages")
    return bool(messages), int(nxt) if isinstance(nxt, int) else cursor


def _rpc_pause(seconds: float) -> None:
    if _RPC_WAKE.wait(seconds):
        _RPC_WAKE.clear()


def start_rpc_waiter() -> None:
    """Daemon thread: while logged in with remote access on, wait on the desktop-rpc
    channel and answer each command from the website mailbox."""
    global _RPC_THREAD
    with _RPC_LOCK:
        if _RPC_THREAD is not None and _RPC_THREAD.is_alive():
            return
        if not (_load_blob().get("device_key") or _load_blob().get("session_value")):
            return
        _RPC_STOP.clear()
        _RPC_WAKE.clear()

        def _loop() -> None:
            cursor: int | None = None
            # A command sent before this wait began (app start, remote access just
            # turned on, after an error): read the mailbox once without being told.
            told = True
            # A refused or failed wait backs off to once a minute, never a tight loop.
            backoff = 5.0
            while not _RPC_STOP.is_set():
                # The site offers Open only for a Live PC, and a PC is Live only
                # with remote access on.
                if not _remote_access_on():
                    told = True
                    _rpc_pause(5.0)
                    continue
                try:
                    if not told:
                        told, cursor = wait_desktop_rpc(cursor)
                    if told:
                        while _poll_desktop_rpc_once() and not _RPC_STOP.is_set():
                            pass
                        told = False
                    backoff = 5.0
                except Exception:
                    told = True
                    _rpc_pause(backoff)
                    backoff = min(backoff * 2, 60.0)

        _RPC_THREAD = __import__("threading").Thread(
            target=_loop, daemon=True, name="duckyos-rpc-wait"
        )
        _RPC_THREAD.start()


def stop_rpc_waiter() -> None:
    _RPC_STOP.set()
    _RPC_WAKE.set()


def send_account_alerts(items: list[dict[str, Any]]) -> bool:
    """One batch of alerts (frontend.notifications) to the account: the profile's bell
    lists them and the site pushes the phone for the ones marked ``push``. One request.

    Each item: ``title``, ``body`` (a status line, never reply text: pushes travel
    through Google's FCM), ``level``, ``kind`` ("chat" | "workflow") + ``id`` (a tap
    opens it in Remote View), ``push``, ``at``. Blocking; never raises. False when
    signed out or the site didn't take it."""
    items = [dict(i) for i in items if isinstance(i, dict)][:20]
    if not items:
        return True
    blob = _load_blob()
    if not account_key(blob):
        return False
    body: dict[str, Any] = {"items": items, "keyId": str(blob.get("device_key_id") or "")}
    try:
        _plugin_collect(
            "uefn-ducky",
            "desktop-notify",
            body,
            unavailable_code="push_unavailable",
            unavailable_msg="Can't send notifications right now.",
            error_code="push_error",
            timeout=12.0,
        )
        return True
    except DuckyOSAccountError as exc:
        if exc.code != "push_unavailable":
            return False
    except Exception:
        return False
    # A site from before desktop-notify keeps only its phone push: one, for the batch.
    pushed = [i for i in items if i.get("push")]
    if not pushed:
        return False
    one = pushed[-1]
    legacy: dict[str, Any] = {"title": str(one.get("title") or "UEFN Ducky")[:80], "body": str(one.get("body") or "")[:160]}
    if len(pushed) > 1:
        legacy = {"title": "UEFN Ducky", "body": f"{len(pushed)} updates on your PC."}
    elif one.get("kind") in ("chat", "workflow") and one.get("id"):
        legacy["kind"] = one["kind"]
        legacy["id"] = str(one["id"])[:80]
    try:
        _plugin_collect(
            "uefn-ducky",
            "desktop-agent-done",
            legacy,
            unavailable_code="push_unavailable",
            unavailable_msg="Can't send notifications right now.",
            error_code="push_error",
            timeout=12.0,
        )
        return True
    except Exception:
        return False


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
        f"/api/v1/plugins/{plugin_id}/collect/{event}",
        body or {},
        prefer_bearer=True,
        allow_anonymous=allow_anonymous,
        timeout=timeout,
    )
    if status == 404:
        # ponytail: 404 often means the site worker is mid-swap, not "plugin off".
        # Never mention plugins/tenants — that copy leaked to the Account tab.
        raise DuckyOSAccountError("Can't connect right now. Try again.", code=unavailable_code)
    if not (200 <= int(status) < 300):
        err = ""
        if isinstance(parsed, dict):
            err = str(parsed.get("error") or "")
            payload = parsed.get("payload")
            if isinstance(payload, dict) and payload.get("error"):
                err = str(payload["error"])
        if re.search(r"plugin|tenant", err, re.I):
            err = ""
        raise DuckyOSAccountError(
            err or "Can't connect right now. Try again.",
            code=error_code,
        )
    if not isinstance(parsed, dict):
        return {}
    payload = parsed.get("payload")
    return payload if isinstance(payload, dict) else parsed


def _collect_payload(event: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One collect call to the account's Store hub (presence and account events)."""
    return _plugin_collect(
        "uefn-ducky-store",
        event,
        body,
        unavailable_code="hub_unavailable",
        unavailable_msg="The Ducky Store is not active on this site yet.",
        error_code="hub_error",
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


def site_url(path: str = "/") -> str:
    """Absolute URL of a path on the signed-in account's website."""
    blob = _load_blob()
    base = str(blob.get("base_url") or resolve_base_url()).rstrip("/")
    path = path if (path or "").startswith("/") else f"/{path or ''}"
    return f"{base}{path}"


def open_site_path(path: str = "/") -> dict[str, Any]:
    """Open a path of the account's website in the system browser."""
    import webbrowser

    url = site_url(path or "/")
    webbrowser.open(url, new=2)
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


def _store_audience_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Who a Store item is for: public, or private to an audience the spaces provider
    reads from the Store row (``audience_id``, ``audience_label``, ``mine``)."""
    try:
        from backend.uefn_plugins.spaces import provider

        extra = provider().catalog_item(raw) or {}
    except Exception:
        extra = {}
    return {
        "visibility": "private" if raw.get("visibility") == "private" else "public",
        "audience_id": str(extra.get("audience_id") or "") or None,
        "audience_label": str(extra.get("audience_label") or ""),
        "mine": extra.get("mine") is True,
    }


def _store_catalog_payload() -> dict[str, Any]:
    """The Store's catalog response. It carries every icon inline (1-2 MB), so on a slow link
    (a phone tether) it gets a minute, not 20 s, which used to leave a new install with no icons.
    A test bed (``DUCKY_STORE_CACHE``) reuses its last catalog for an hour per account, so each
    fresh profile has them at once; real installs always ask the Store."""
    cache = _store_cache_dir()
    path = cache / f"catalog-{account_key(_load_blob()) or 'anonymous'}.json" if cache else None
    if path and path.is_file() and time.time() - path.stat().st_mtime < 3600:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    payload = _store_collect("catalog", {}, allow_anonymous=True, timeout=60.0)
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass  # a cache that can't be written only means asking again
    return payload


def store_catalog() -> dict[str, Any]:
    """Published store items + local install/update state for skills and UEFN plugins.

    Fetched as the signed-in account when there is one (``api_request`` attaches
    its credentials even with ``allow_anonymous``), so private items shared with the
    account show; signed out it is the anonymous public catalog.
    """
    try:
        payload = _store_catalog_payload()
    except DuckyOSAccountError as exc:
        # Transient 403 right after a large download — one quiet retry.
        msg = (exc.message or "").lower()
        if "forbidden" in msg or exc.code in ("store_error", "forbidden"):
            try:
                __import__("time").sleep(0.45)
                payload = _store_catalog_payload()
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
                    "version": pack.get("version") or 0,
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
        audience = _store_audience_fields(raw)
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
                            **audience,
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
                sk_ver = sk.get("version") or 0
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
                **audience,
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
                "contributes_summary": _plugin_contributes_summary(plug),
                **_local_plugin_icon_fields(plug),
            }
        )
    # Private audiences are optional: only when the spaces provider says so does the
    # library show its Shared category.
    try:
        from backend.uefn_plugins.spaces import provider

        extras = provider().catalog_extras(payload) or {}
    except Exception:
        extras = {}
    audiences = extras.get("audiences") is True
    return {
        "ok": True,
        "items": items_out,
        "audiences": audiences,
        "audienceNotes": list(extras.get("audienceNotes") or []) if audiences else [],
        "audienceCategoryLabel": str(extras.get("audienceCategoryLabel") or "Shared"),
        "audienceEmpty": extras.get("audienceEmpty") if audiences and isinstance(extras.get("audienceEmpty"), dict) else None,
    }


def store_item_versions(slug: str, latest_version: str | None = None) -> dict[str, Any]:
    """Public changelog rows for a Store item (paginated in the panel UI).

    Cache-first: refetch only when ``latest_version`` differs from the stored
    version (or there is no cache). Offline returns cached rows.
    """
    sid = str(slug or "").strip()
    if not sid:
        return {"ok": False, "error": "slug required", "code": "bad_request", "versions": []}
    want = str(latest_version or "").strip()
    from frontend.patch_notes_cache import get as cache_get
    from frontend.patch_notes_cache import put as cache_put

    cached = cache_get(f"item:{sid}")
    if cached and (not want or cached.get("version") == want):
        return {"ok": True, "slug": sid, "versions": cached["versions"], "cached": True}
    try:
        payload = _store_collect(
            "item-versions",
            {"slug": sid},
            allow_anonymous=True,
            timeout=20.0,
        )
    except DuckyOSAccountError as exc:
        if cached:
            return {
                "ok": True,
                "slug": sid,
                "versions": cached["versions"],
                "cached": True,
                "error": exc.message,
                "code": exc.code,
            }
        return {"ok": False, "error": exc.message, "code": exc.code, "versions": []}
    raw = payload.get("versions") if isinstance(payload.get("versions"), list) else []
    versions: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ver = str(row.get("version") or "").strip()
        if not ver:
            continue
        created = str(row.get("created_at") or "").strip()
        versions.append(
            {
                "version": ver,
                "changelog": str(row.get("changelog") or "").strip(),
                "created_at": created or None,
            }
        )
    stored = want or (versions[0]["version"] if versions else "")
    cache_put(f"item:{sid}", stored, versions)
    return {"ok": True, "slug": sid, "versions": versions}


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


# Concurrent Store installs each call reload_plugins() — that freezes the panel UI.
# Serialize end-to-end so Update All stays responsive (JS also queues; this is the belt).
_STORE_INSTALL_LOCK = __import__("threading").Lock()


def store_download_and_install(
    slug: str,
    *,
    version: str | None = None,
    replace: bool = True,
    is_update: bool = False,
    replace_old: bool = False,
) -> dict[str, Any]:
    """Download a published skill or plugin zip and install into AppData.

    Sent as the signed-in account when there is one (private items are only for
    their audience, server-enforced); anonymous otherwise.

    Pass ``is_update=True`` when the pack is already installed locally so the
    Store does not increment ``install_count``. ``replace_old=True`` lets it remove the
    plugins this one replaces (``uefn_takes_over``), only once the new copy checked out.
    """
    with _STORE_INSTALL_LOCK:
        result = _store_download_and_install_unlocked(
            slug,
            version=version,
            replace=replace,
            is_update=is_update,
            replace_old=replace_old,
        )
    if not is_update:
        try:
            from frontend import usage_stats

            usage_stats.plugin_installed(slug)
        except Exception:
            pass
    return result


# Matches backend.uefn_plugins.store.MAX_PLUGIN_ZIP_BYTES: nothing bigger installs anyway.
_STORE_ZIP_MAX_BYTES = 100 * 1024 * 1024


def _fetch_store_zip_url(url: str, size: int, *, timeout: float = 120.0) -> bytes:
    """Fetch a big Store zip from the short-lived link the Store answered with.

    Sent with the app's own User-Agent (the site's CDN refuses urllib's default),
    resumed with Range after every dropped connection (some networks cut storage
    downloads every few hundred KB), and capped at the announced size so a wrong
    link can't fill the disk.
    """
    import io
    from urllib.parse import urlparse

    from frontend.update_network import download_resumable

    if urlparse(url).scheme not in ("https", "http"):
        raise DuckyOSAccountError("Store download link is not a web address", code="store_bad_url")
    cap = size if 0 < size <= _STORE_ZIP_MAX_BYTES else _STORE_ZIP_MAX_BYTES

    def check_cap(done: int, _total: int) -> None:
        if done > cap:
            raise DuckyOSAccountError("Store download is bigger than announced", code="store_too_big")

    buf = io.BytesIO()
    try:
        download_resumable(
            url,
            buf,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/zip, application/octet-stream, */*"},
            timeout=timeout,
            on_chunk=check_cap,
        )
    except urllib.error.HTTPError as exc:
        raise DuckyOSAccountError(f"Store download failed (HTTP {exc.code})", code="store_download") from exc
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        host = urlparse(url).netloc
        got = buf.tell()
        raise DuckyOSAccountError(
            f"Store download from {host} kept dropping ({got:,} of {size:,} bytes): {exc}", code="network"
        ) from exc
    return buf.getvalue()


def _store_cache_dir() -> Path | None:
    """Test beds only: ``DUCKY_STORE_CACHE`` is a folder that outlives each fresh test profile,
    holding Store zips and the catalog. Never set in a real install."""
    import os

    root = os.environ.get("DUCKY_STORE_CACHE", "").strip()
    return Path(root) if root else None


def _store_zip_cache(sha256: str) -> Path | None:
    """A test bed's copy of a linked Store zip, by its sha256: a fresh profile installs without
    downloading it again. The cached bytes still pass the same size and hash checks."""
    root = _store_cache_dir()
    if not root or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    return root / f"{sha256}.zip"


def store_zip_bytes(payload: dict[str, Any]) -> bytes:
    """The zip a Store response carries — inline ``zipB64`` (small) or a short-lived
    ``url`` (big, e.g. compiled plugins) — checked against the response's sha256 and size
    before anything uses it."""
    import base64
    import hashlib

    zip_b64 = str(payload.get("zipB64") or "")
    url = str(payload.get("url") or "").strip()
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    expected = str(payload.get("sha256") or "").strip().lower()
    if not zip_b64 and not url:
        # The Store says why it held the zip back (e.g. "… needs UEFN Ducky 1.2.357").
        refused = str(payload.get("error") or "").strip()
        if refused:
            raise DuckyOSAccountError(refused, code="store_refused")
        raise DuckyOSAccountError("Store download returned no zip", code="store_empty")
    if not expected:
        # Neither inline bytes nor a link are trusted without the hash that comes with them.
        raise DuckyOSAccountError("Store download missing sha256", code="store_hash_missing")
    if zip_b64:
        try:
            raw = base64.b64decode(zip_b64)
        except Exception as exc:
            raise DuckyOSAccountError(f"Invalid zip data: {exc}", code="store_bad_zip") from exc
    else:
        cached = _store_zip_cache(expected)
        raw = cached.read_bytes() if cached and cached.is_file() else _fetch_store_zip_url(url, size)
    if size and len(raw) != size:
        raise DuckyOSAccountError("Downloaded zip has the wrong size", code="store_hash_mismatch")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise DuckyOSAccountError("Downloaded zip failed hash check", code="store_hash_mismatch")
    if url:
        cached = _store_zip_cache(expected)
        if cached and not cached.is_file():
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(raw)
            except OSError:
                pass  # a cache that can't be written only means downloading again
    return raw


def _store_download_and_install_unlocked(
    slug: str,
    *,
    version: str | None = None,
    replace: bool = True,
    is_update: bool = False,
    replace_old: bool = False,
) -> dict[str, Any]:
    import io
    import zipfile

    slug = (slug or "").strip()
    if not slug:
        raise DuckyOSAccountError("slug required", code="bad_request")
    body: dict[str, Any] = {"slug": slug, "isUpdate": bool(is_update)}
    if version:
        body["version"] = str(version).strip()
    # Signed in → sent as the account (api_request attaches it); signed out → anonymous.
    payload = _store_collect("download", body, allow_anonymous=True, timeout=120.0)
    # Small zips inline, big ones by link — verified (sha256 + size) before the install
    # and signature checks below ever see the bytes.
    raw = store_zip_bytes(payload)

    is_plugin = False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            is_plugin = any(Path(n).name == "plugin.json" for n in zf.namelist())
    except zipfile.BadZipFile as exc:
        raise DuckyOSAccountError(f"Invalid zip: {exc}", code="store_bad_zip") from exc

    if is_plugin:
        from backend.uefn_plugins.signing import record_from_download
        from backend.uefn_plugins.store import import_plugin_from_bytes

        # The server signs each version once; pass the signed record so the install can
        # verify it and keep it next to the plugin for the load-time license check.
        signed_record = record_from_download(payload)
        result = import_plugin_from_bytes(
            raw, source="store", replace=bool(replace), signed_record=signed_record, replace_old=replace_old
        )
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


_AUTO_APPLY_LOCK = __import__("threading").Lock()
_auto_apply_ran = False

# The start-up pass is one shot: offline at launch, a catalog hiccup, or sign-in
# arriving a moment later used to mean no plugin updates until the next restart,
# and a user sat on a stale plugin for days. So the pass is repeated early on and
# then once in a while for as long as the app runs.
STORE_UPDATE_RETRY_DELAYS_S = (120.0, 600.0, 1800.0)
STORE_UPDATE_PERIOD_S = 6 * 3600.0
_retry_thread_started = False


def _store_update_retry_loop(wait, run, *, delays=STORE_UPDATE_RETRY_DELAYS_S, period=STORE_UPDATE_PERIOD_S) -> int:
    """Run ``run()`` after each delay, then every ``period``; ``wait(seconds)`` returns
    True to stop. Returns how many passes ran (for tests)."""
    passes = 0
    for delay in delays:
        if wait(delay):
            return passes
        run()
        passes += 1
    while True:
        if wait(period):
            return passes
        run()
        passes += 1


def start_store_update_retries() -> None:
    """Keep re-running the Store auto-update pass in the background (once per process)."""
    global _retry_thread_started
    if _retry_thread_started:
        return
    _retry_thread_started = True
    stop = threading.Event()

    def _run() -> None:
        try:
            auto_apply_store_updates(force=True)
        except Exception:
            pass

    threading.Thread(
        target=_store_update_retry_loop,
        args=(stop.wait, _run),
        name="store-update-retries",
        daemon=True,
    ).start()


def auto_apply_store_updates(*, force: bool = False) -> dict[str, Any]:
    """Quietly install newer Store versions of already-installed plugins/skills.

    End users never open Settings → Store for a published fix. Local/AI
    sources are skipped. ponytail: one catalog pass
    per process; ceiling is offline/catalog-down until the next panel start.
    """
    global _auto_apply_ran
    with _AUTO_APPLY_LOCK:
        if _auto_apply_ran and not force:
            return {"ok": True, "updated": [], "skipped": "already"}
        cat = store_catalog()
        if not cat.get("ok"):
            return {
                "ok": False,
                "updated": [],
                "error": cat.get("error"),
                "code": cat.get("code"),
            }
        from backend.uefn_plugins.store import install_stopped_halfway, uefn_parts_without_uefn

        updated: list[str] = []
        errors: list[dict[str, Any]] = []
        for item in cat.get("items") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") not in ("plugin", "skill"):
                continue
            if item.get("source") in ("local", "ai"):
                continue
            slug = str(item.get("slug") or "").strip()
            if not slug:
                continue
            # A plugin still on that lost its plugin.json (an install that stopped halfway,
            # before installs were staged) comes back the way an update does.
            if item.get("state") != "update" and not (
                item.get("state") == "available" and item.get("kind") == "plugin" and install_stopped_halfway(slug)
            ):
                continue
            try:
                # replace_old: UEFN 1.1 takes the old UEFN parts' place, which go only once it lands.
                result = store_download_and_install(slug, replace=True, is_update=True, replace_old=True)
            except DuckyOSAccountError as exc:
                errors.append({"slug": slug, "error": exc.message, "code": exc.code})
                continue
            except Exception as exc:
                errors.append({"slug": slug, "error": str(exc)})
                continue
            if result.get("ok"):
                updated.append(slug)
            else:
                errors.append({"slug": slug, "error": result.get("error")})
        # The UEFN parts moved into one UEFN plugin, and people don't know: a PC that still
        # has some of them but no UEFN gets UEFN, and the parts go only once it has landed.
        if uefn_parts_without_uefn():
            try:
                result = store_download_and_install("uefn", replace=True, is_update=False, replace_old=True)
                if result.get("ok"):
                    updated.append("uefn")
                else:
                    errors.append({"slug": "uefn", "error": result.get("error")})
            except Exception as exc:
                errors.append({"slug": "uefn", "error": getattr(exc, "message", None) or str(exc)})
        _auto_apply_ran = True
        if updated or errors:
            # One line in activity.jsonl, so "why is my plugin still old?" has an answer.
            try:
                from frontend.error_log import record_activity

                record_activity(
                    "store",
                    "auto-update: "
                    + (("updated " + ", ".join(updated)) if updated else "nothing updated")
                    + (("; failed " + ", ".join(f"{e.get('slug')}: {e.get('error')}" for e in errors)) if errors else ""),
                )
            except Exception:
                pass
        if updated:
            try:
                from frontend.ui_web.agent_modes import push_ui_event

                push_ui_event({"type": "uefn_plugins_changed"})
            except Exception:
                pass
        return {"ok": True, "updated": updated, "errors": errors}
