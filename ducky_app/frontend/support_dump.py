"""Discord-pasteable 24h support dump — plugins, agents, errors, activity. No secrets."""

from __future__ import annotations

import re
import time

from frontend.error_log import format_entries, read_activity, read_errors, trim

_DISCORD_SOFT_CAP = 1800
_SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)"
)


def format_support_dump(*, max_chars: int = _DISCORD_SOFT_CAP) -> str:
    """Plain text for Discord. Keys are never included — only yes/no."""
    trim()
    lines = [
        f"UEFN-Ducky support dump  {_stamp(time.time())}",
        "Privacy: errors and plugin/app status only — no chats, keys, or personal files.",
        f"App: {_app_version()}",
        f"Listener: {_listener_line()}",
        f"Default model: {_default_model() or '(none)'}",
        f"Keys: {_key_line()}",
        "Plugins:",
        *_plugin_lines(),
        "Agents:",
        *_agent_lines(),
    ]
    errors = format_entries(read_errors(limit=20))
    activity = format_entries(read_activity(limit=20))
    lines.append(f"Errors (24h, newest {len(errors)}):")
    lines.extend(errors or ["  (none)"])
    lines.append(f"Log (24h, newest {len(activity)}):")
    lines.extend(activity or ["  (none)"])
    text = _redact("\n".join(lines))
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 48].rstrip() + "\n… truncated — copy Log / Errors for the rest."
    return text


def _stamp(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (ValueError, TypeError, OSError):
        return "?"


def _app_version() -> str:
    try:
        from frontend import __version__

        return str(__version__)
    except Exception:
        return "?"


def _default_model() -> str:
    try:
        from frontend.settings import PanelSettings

        return str(getattr(PanelSettings.load(), "default_model", "") or "").strip()
    except Exception:
        return ""


def _listener_line() -> str:
    try:
        from backend.bridge.status import fetch_listener_status
        from frontend import __version__
        from frontend.settings import PANEL_LISTENER_PORT, PanelSettings

        st = fetch_listener_status(
            PANEL_LISTENER_PORT,
            version=__version__,
            selected_project_root=PanelSettings.load().uefn_project_root,
        )
        if not isinstance(st, dict):
            return "unknown"
        bits = ["online" if st.get("online") else "offline"]
        if st.get("wedged"):
            bits.append("wedged")
        name = str(st.get("uefn_project_name") or "").strip()
        if name:
            bits.append(name)
        return " · ".join(bits)
    except Exception:
        return "unknown"


def _key_line() -> str:
    try:
        from backend.agent.secrets import has_key
        from backend.uefn_plugins.host import get_contributions

        rows = get_contributions().get("llm_providers") or []
        parts: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or row.get("secret_key") or "").strip().lower()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            parts.append(f"{pid}={'yes' if has_key(pid) else 'no'}")
        return " ".join(parts) or "(none)"
    except Exception:
        return "(unavailable)"


def _plugin_lines() -> list[str]:
    try:
        from backend.uefn_plugins.store import list_uefn_plugins

        rows = list_uefn_plugins()
    except Exception:
        return ["  (unavailable)"]
    if not rows:
        return ["  (none installed)"]
    out: list[str] = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "?").strip()
        ver = str(p.get("version") or "?").strip()
        state = "on" if p.get("enabled") else "off"
        out.append(f"  {pid} {ver} {state}")
    return out or ["  (none installed)"]


def _agent_lines() -> list[str]:
    try:
        from backend.agent.coding_agents.base import detect_all

        payload = detect_all()
    except Exception:
        return ["  (unavailable)"]
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, list) or not agents:
        return ["  (none)"]
    out: list[str] = []
    for a in agents:
        if not isinstance(a, dict):
            continue
        label = str(a.get("label") or a.get("id") or "?").strip()
        if not a.get("enabled", True):
            state = "disabled"
        elif a.get("available"):
            state = "available"
        else:
            state = "unavailable"
        status = str(a.get("status") or "").strip()
        extra = f" — {status}" if status else ""
        out.append(f"  {label}: {state}{extra}")
    return out or ["  (none)"]


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text)
