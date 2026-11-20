"""Anonymous counts of what UEFN itself does to agents (popups, deadlines, builds, memory,
Epic's answers), each tagged with the UEFN release, so Epic feedback reads per release.

Words and buckets only (usage_events.EVENTS validates them): never a path, title or message.
Every helper returns at once and never raises; the event is sent on a daemon thread.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_RELEASE_TTL_S = 300.0
_release_cache: dict[str, Any] = {"at": 0.0, "value": ""}
_DESCRIBE_TOOLS = ("list_toolsets", "describe_toolset")
_status_day: dict[str, str] = {}  # state -> UTC day already sent, so a probe reads no db


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="uefn-event", daemon=True).start()


def uefn_release() -> str:
    """The running UEFN's release ("43.10", from Engine/Build/Build.version), or ""."""
    now = time.monotonic()
    if now - _release_cache["at"] < _RELEASE_TTL_S:
        return _release_cache["value"]
    value = ""
    try:
        import psutil

        for proc in psutil.process_iter(["name", "exe"]):
            if str(proc.info.get("name") or "").lower().startswith("unrealeditorfortnite"):
                value = release_of(Path(proc.info.get("exe") or ""))
                if value:
                    break
    except Exception:  # noqa: BLE001
        value = ""
    _release_cache.update(at=now, value=value)
    return value


def release_of(exe: Path) -> str:
    """"43.10" from the Build.version beside UEFN's exe (BranchName "++Fortnite+Release-43.10")."""
    try:
        data = json.loads((exe.parent.parent.parent / "Build" / "Build.version").read_text(encoding="utf-8"))
        found = re.search(r"Release-(\d+\.\d+)", str(data.get("BranchName") or ""))
        return found.group(1) if found else ""
    except Exception:  # noqa: BLE001
        return ""


def _send(name: str, with_release: bool = True, **props: Any) -> None:
    def run() -> None:
        try:
            from frontend import usage_stats

            if with_release:
                props["uefn"] = uefn_release()
            usage_stats.track(name, **props)
        except Exception:  # noqa: BLE001 — counting never breaks a call
            pass

    try:
        _spawn(run)
    except Exception:  # noqa: BLE001
        pass


def popup_kind(title: str) -> str:
    t = (title or "").lower()
    for word, kind in (("verse", "verse_error"), ("crash", "crash"), ("check out", "checkout"),
                       ("checkout", "checkout"), ("save", "save"), ("message", "message")):
        if word in t:
            return kind
    return "other"


def deadline(why: dict[str, Any], heavy: bool, steps: int) -> None:
    """An Epic UEFN call hit its own deadline; ``why`` is call_limits.stall_reason's answer."""
    _send("epic.deadline", reason=str(why.get("reason") or ""), heavy="y" if heavy else "n",
          steps="many" if steps > 1 else "one")


def popup(title: str, during: str) -> None:
    """A UEFN window held a call or a build (``during``: call | build)."""
    _send("uefn.popup", kind=popup_kind(title), during=during)


def verse_build(result: str, took_s: float) -> None:
    _send("verse.build", result=result, took_s=took_s)


def memory(grown_gb: float | None, freed_gb: float) -> None:
    props: dict[str, Any] = {"freed_gb": freed_gb}
    if grown_gb is not None:
        props["grown_gb"] = grown_gb
    _send("uefn.memory", **props)


def epic_status(reason: str) -> None:
    """Epic's MCP port state, once per UTC day per state."""
    try:
        from frontend import usage_stats

        state = {"": "online", "unreachable": "unreachable", "disabled": "disabled",
                 "port_taken": "port_taken", "other_editor": "other_editor"}.get(reason, "other")
        key = f"uefn_events.epic_status.{state}"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _status_day.get(state) == today:
            return
        _status_day[state] = today
        if usage_stats._meta_get(key) == today:
            return
        usage_stats._meta_set(key, today)
    except Exception:  # noqa: BLE001
        return
    _send("epic.status", state=state)


def lease(waited_s: float, gave_up: bool) -> None:
    """A chat that waited for another chat's editor lease (never sent without a wait)."""
    if waited_s > 0:
        _send("editor.lease", with_release=False, outcome="gave_up" if gave_up else "waited", waited_s=waited_s)


def answer(tool: str, text: str) -> None:
    """Look at one successful Epic answer: discovery size, and answers that read as success but aren't."""
    try:
        if tool in _DESCRIBE_TOOLS:
            _send("epic.describe", tool=tool, chars_n=len(text))
        compact = text.replace(" ", "")
        if '"returnValue":"{}"' in compact:
            _send("epic.answer", kind="empty_props")
        elif "VerseStringProperty" in text and "not implemented" in text:
            _send("epic.answer", kind="verse_string")
    except Exception:  # noqa: BLE001
        pass
