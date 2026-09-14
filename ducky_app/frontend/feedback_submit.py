"""POST Support-tab feedback to the public uefnducky.org Forms collect endpoint."""

from __future__ import annotations

from typing import Any

from backend.util.http import HttpError, http_json
from frontend.support_dump import _redact

FORM_ID = "uefn-ducky-feedback"
COLLECT_URL = "https://uefnducky.org/api/v1/plugins/custom-forms/collect/submit"
ORIGIN = "https://uefnducky.org"
ERROR_LOG_MAX_BYTES = 8000


def build_payload(
    *,
    message: str,
    email: str = "",
    error_log: str = "",
    app_version: str = "",
) -> dict[str, str]:
    """Public collect body. Extra keys are dropped by the form allowlist."""
    body = {
        "formId": FORM_ID,
        "message": message.strip(),
        "source": "desktop",
        "app_version": (app_version or "").strip(),
        "_hp": "",
    }
    email = email.strip()
    if email:
        body["email"] = email
    clipped = _clip_error_log(error_log)
    if clipped:
        body["error_log"] = clipped
    return body


def submit_feedback(
    *,
    message: str,
    email: str = "",
    include_errors: bool = False,
) -> dict[str, Any]:
    """POST to the official form. Never uses the user's tenant URL."""
    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "Write a short message first."}
    error_log = ""
    if include_errors:
        error_log = _current_error_log()
    from frontend import __version__

    body = build_payload(
        message=text,
        email=email or "",
        error_log=error_log,
        app_version=str(__version__),
    )
    headers = {
        "Origin": ORIGIN,
        "User-Agent": f"UEFN-Ducky/{__version__}",
    }
    try:
        http_json("POST", COLLECT_URL, headers=headers, json_body=body, timeout=20)
    except HttpError as exc:
        return {"ok": False, "error": _user_error(exc)}
    return {"ok": True}


def _current_error_log() -> str:
    from frontend.error_log import format_entries, read_errors, trim

    trim()
    lines = format_entries(read_errors())
    return "\n".join(lines)


def _clip_error_log(raw: str) -> str:
    text = _redact((raw or "").strip())
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= ERROR_LOG_MAX_BYTES:
        return text
    return encoded[:ERROR_LOG_MAX_BYTES].decode("utf-8", "ignore").rstrip()


def _user_error(exc: HttpError) -> str:
    if exc.status == 403:
        return "Could not reach the feedback form (blocked)."
    if exc.status == 429:
        return "Too many submissions — try again in a minute."
    if exc.status and 400 <= exc.status < 500:
        detail = exc.detail
        if isinstance(detail, dict):
            msg = str(detail.get("error") or detail.get("message") or "").strip()
            if msg:
                return msg[:200]
        return "The form rejected that message."
    return "Could not send feedback — check your connection."
