"""Wiring preflight: auto-compile + wait for hashes when Verse reflection is stale.

Host-side only (never runs inside the UEFN listener). A wire/set call that fails
because the Verse class was not built yet ("STALE REFLECTION", "no compiled
hash", "Verse class not found", …) compiles once, reloads the listener, waits
until ``get_verse_editables`` shows that field readable, then retries ONCE.
``[WinError 10054]`` means the build started — same wait, no extra compile.

A field that is still stale after that wait+retry is locked: further wire_*
calls on the same device+field return immediately (no compile, no editor, no
ledger spam). Unlock when ``get_verse_editables`` shows a mangled hash.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterable

STALE_MARKERS: tuple[str, ...] = (
    "STALE REFLECTION",
    "no compiled hash",
    "Verse behavior not found",
    "Verse class not found",
    "not found under _Verse. Recompile",
)

AUTO_RECOVERED = "compiled + reloaded listener"
AUTO_RESYNCED = "resynced listener"
UNKNOWN_FIELD_MARKER = "Unknown Verse field"
NEXT_FIX_ERRORS = "fix the Verse errors, then retry"
NEXT_OPEN_UEFN = "Open the project in UEFN and run workspace_compile_verse, then retry once"
NEXT_STALE_LOCKED = (
    "Hashes still missing after one compile+reload. Call get_verse_editables on "
    "THIS SAME device. Do NOT call wire_* again until readable is true. "
    "Do not compile-loop. Do not place a second copy of the device. "
    "Check the field is still @editable in the .verse; do not refactor the class to avoid it."
)
NEXT_UNKNOWN_FIELD = (
    "Field is not on this device's Script. Call get_verse_editables on THIS SAME "
    "device and use an exact key. Do not compile. Do not retry this field on this actor."
)
# ponytail: 90s ceiling covers a typical Verse relink; raise HASH_WAIT_S if islands
# regularly take longer, or poll list_verse_types first if inspect load is an issue.
HASH_WAIT_S = 90.0
HASH_POLL_S = 3.0
HASH_POLL_MAX_S = 8.0

_lock = threading.Lock()
# (actor_ident, field) → last error. actor_ident is a lowercase label or path tail.
_stale_fields: dict[tuple[str, str], str] = {}


def is_stale_reflection_error(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(marker.lower() in low for marker in STALE_MARKERS)


def is_unknown_verse_field_error(text: str | None) -> bool:
    """Pre-CRC32 AppData listener: hash cache miss, not a missing Verse field."""
    return bool(text) and UNKNOWN_FIELD_MARKER.lower() in text.lower()


def is_build_started_error(text: str | None) -> bool:
    """Workflow RPC died because UEFN tore the socket down to start a Verse build."""
    if not text:
        return False
    low = text.lower()
    return (
        "10054" in text
        or "connection reset" in low
        or "forcibly closed" in low
        or "previous link task did not complete" in low
    )


def _now() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _inspect_editables(actor_path: str) -> dict[str, Any] | None:
    try:
        from backend.bridge import send_command

        raw = send_command(
            "get_verse_editables",
            {"actor_path": actor_path, "include_wiring_hints": False},
        )
    except Exception:  # noqa: BLE001 — inspect must not mask the original wire error
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw if isinstance(raw, dict) else None


def _editable_info(payload: dict[str, Any], field: str) -> dict[str, Any] | None:
    editables = payload.get("editables")
    if not isinstance(editables, dict):
        return None
    if field in editables and isinstance(editables[field], dict):
        return editables[field]
    key = field.lower()
    for name, info in editables.items():
        if str(name).lower() == key and isinstance(info, dict):
            return info
    return None


def _editable_keys(payload: dict[str, Any] | None) -> list[str]:
    if not payload or not isinstance(payload.get("editables"), dict):
        return []
    return [str(k) for k in payload["editables"]]


def _similar_fields(field: str, keys: Iterable[str]) -> list[str]:
    fl = (field or "").lower()
    if not fl:
        return []
    out: list[str] = []
    for key in keys:
        kl = str(key).lower()
        if fl in kl or kl in fl:
            out.append(str(key))
    return out


def field_readable_on_device(actor_path: str, field: str) -> bool:
    if not actor_path or not field:
        return True
    payload = _inspect_editables(actor_path)
    if not payload:
        return False
    if payload.get("all_readable"):
        note_resolved_fields(actor_path, [field])
        return True
    info = _editable_info(payload, field)
    if info and info.get("readable") is True:
        note_resolved_fields(actor_path, [field])
        return True
    return False


def wait_for_field_hash(actor_path: str, field: str) -> bool:
    """Poll get_verse_editables until *field* is readable, or HASH_WAIT_S elapses."""
    if not actor_path or not field:
        return True
    deadline = _now() + HASH_WAIT_S
    delay = HASH_POLL_S
    while True:
        if field_readable_on_device(actor_path, field):
            return True
        remaining = deadline - _now()
        if remaining <= 0:
            return False
        _sleep(min(delay, remaining))
        delay = min(HASH_POLL_MAX_S, delay + 1.0)


def _actor_idents(actor_path: str) -> tuple[str, ...]:
    raw = (actor_path or "").strip().lower()
    if not raw:
        return ()
    tail = raw.rsplit(".", 1)[-1]
    return (raw, tail) if tail != raw else (raw,)


def _field_key(field: str) -> str:
    return (field or "").strip().lower()


def reset_stale_locks() -> None:
    """Tests only."""
    with _lock:
        _stale_fields.clear()


def stale_lock_reason(actor_path: str, field: str) -> str | None:
    field_key = _field_key(field)
    if not field_key:
        return None
    with _lock:
        for ident in _actor_idents(actor_path):
            hit = _stale_fields.get((ident, field_key))
            if hit:
                return hit
    return None


def lock_stale_field(actor_path: str, field: str, error: str) -> None:
    field_key = _field_key(field)
    idents = _actor_idents(actor_path)
    if not field_key or not idents:
        return
    with _lock:
        for ident in idents:
            _stale_fields[(ident, field_key)] = error


def unlock_stale_field(actor_path: str, field: str) -> None:
    field_key = _field_key(field)
    if not field_key:
        return
    with _lock:
        for ident in _actor_idents(actor_path):
            _stale_fields.pop((ident, field_key), None)


def note_resolved_fields(actor_path: str, fields: Iterable[str]) -> None:
    """Clear locks for fields that now have a compiled hash."""
    for name in fields:
        if name:
            unlock_stale_field(actor_path, str(name))


def _stale_locked_payload(tool_name: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool_name,
        "error": error,
        "stale_locked": True,
        "next": NEXT_STALE_LOCKED,
    }


def error_text(result: Any) -> str | None:
    """Return the error text when *result* is an error envelope, else None."""
    if isinstance(result, dict):
        if result.get("ok") is False or result.get("success") is False:
            return json.dumps(result, ensure_ascii=False, default=str)
        if result.get("error") and result.get("ok") is not True and result.get("success") is not True:
            return json.dumps(result, ensure_ascii=False, default=str)
        return None
    if isinstance(result, str):
        stripped = result.strip()
        if stripped.startswith("ERROR:"):
            return stripped
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            return None
        return error_text(obj) if isinstance(obj, dict) else None
    return None


def add_field(result: Any, key: str, value: Any) -> Any:
    """Add *key* to a dict result or to a JSON-object string result (parse-add-dump)."""
    if isinstance(result, dict):
        out = dict(result)
        out[key] = value
        return out
    if isinstance(result, str):
        try:
            obj = json.loads(result)
        except (ValueError, TypeError):
            return result
        if isinstance(obj, dict):
            obj[key] = value
            return json.dumps(obj, ensure_ascii=False, default=str)
    return result


def _record_failure(tool_name: str, message: str) -> None:
    try:
        from backend.tools.verse.verse_stats import record_tool_failure
    except ImportError:
        return
    try:
        record_tool_failure(tool_name, message)
    except Exception:  # noqa: BLE001 — stats must never break a tool
        pass


def _compile_verse() -> dict[str, Any]:
    """Run workspace_compile_verse and return its parsed payload (raises when unavailable)."""
    from backend.tools.verse.verse_diagnostics import workspace_compile_verse

    raw = workspace_compile_verse()
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        payload = {"compile": {"raw": str(raw)}}
    return payload if isinstance(payload, dict) else {"compile": {"raw": str(raw)}}


def _reload_listener() -> str:
    from backend.tools.core.system import reload_listener

    try:
        return str(reload_listener())
    except Exception as exc:  # noqa: BLE001 — a reload hiccup must not mask the retry
        return f"reload_listener failed: {exc}"


def _resync_stale_listener() -> str:
    """Force AppData listener copy from this EXE, then reload UEFN Python."""
    try:
        from frontend.deploy import sync_listener_to_appdata

        sync_listener_to_appdata(force=True)
    except Exception as exc:  # noqa: BLE001 — still try reload; retry may work
        return f"sync_listener_to_appdata failed: {exc}"
    return _reload_listener()


def _one_retry(
    call: Callable[[], Any],
    *,
    tool_name: str,
    actor_path: str,
    field: str,
    recovered_tag: str,
) -> Any:
    try:
        retried = call()
    except Exception as exc:  # noqa: BLE001 — classify stale vs other
        err2 = str(exc)
        if is_stale_reflection_error(err2):
            lock_stale_field(actor_path, field, err2)
            return _stale_locked_payload(tool_name, err2)
        raise
    err2 = error_text(retried)
    if err2 and is_stale_reflection_error(err2):
        lock_stale_field(actor_path, field, err2)
        return _stale_locked_payload(tool_name, err2)
    if actor_path and field:
        unlock_stale_field(actor_path, field)
    return add_field(retried, "auto_recovered", recovered_tag)


def _wait_then_retry(
    call: Callable[[], Any],
    *,
    tool_name: str,
    actor_path: str,
    field: str,
    err: str,
) -> Any:
    if not wait_for_field_hash(actor_path, field):
        lock_stale_field(actor_path, field, err)
        return _stale_locked_payload(tool_name, err)
    return _one_retry(
        call,
        tool_name=tool_name,
        actor_path=actor_path,
        field=field,
        recovered_tag=AUTO_RECOVERED,
    )


def _unknown_field_after_resync(
    *,
    tool_name: str,
    actor_path: str,
    field: str,
    err: str,
    raised: BaseException | None,
    result: Any,
) -> Any:
    """After one listener resync: missing key → lock; otherwise propagate."""
    if actor_path and field:
        payload = _inspect_editables(actor_path)
        keys = _editable_keys(payload)
        if payload is not None and _editable_info(payload, field) is None:
            similar = _similar_fields(field, keys)
            msg = (
                f"Unknown Verse field {field!r} — not on this device's Script. "
                f"Actual fields: {keys[:20]}"
            )
            if similar:
                msg += f". Similar: {similar}"
            lock_stale_field(actor_path, field, msg)
            return {
                "ok": False,
                "tool": tool_name,
                "error": msg,
                "stale_locked": True,
                "fields": keys,
                "similar": similar,
                "next": NEXT_UNKNOWN_FIELD,
            }
    if raised is not None:
        raise raised
    return result


def run_with_build_retry(
    call: Callable[[], Any],
    *,
    tool_name: str,
    actor_path: str = "",
    field: str = "",
) -> Any:
    """Call *call*; on stale reflection compile once, wait for the hash, retry once.

    ``Unknown Verse field`` is a stale AppData listener (pre-CRC32), not a missing
    Verse field — force-sync the listener, reload, retry once. No Verse compile.
    If the field is still absent on this Script after that, lock it.

    Returns whatever ``call()`` returns. A recovered retry result carries
    ``auto_recovered`` = ``"compiled + reloaded listener"`` or ``"resynced listener"``.
    Non-stale failures are returned / re-raised unchanged.

    A field that stays stale after that wait+retry is locked so the next wire_* on
    the same device+field does not compile, hit the editor, or write another
    ledger row.
    """
    locked = stale_lock_reason(actor_path, field)
    if locked:
        return _stale_locked_payload(tool_name, locked)

    raised: BaseException | None = None
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 — classify below
        raised = exc
        err = str(exc)
    else:
        err = error_text(result)
        if err is None:
            if actor_path and field:
                unlock_stale_field(actor_path, field)
            return result

    _record_failure(tool_name, err)
    if is_unknown_verse_field_error(err):
        _resync_stale_listener()
        try:
            retried = call()
        except Exception as exc:  # noqa: BLE001 — one resync only; no Verse compile
            return _unknown_field_after_resync(
                tool_name=tool_name,
                actor_path=actor_path,
                field=field,
                err=str(exc),
                raised=exc,
                result=None,
            )
        err2 = error_text(retried)
        if err2 is None:
            return add_field(retried, "auto_recovered", AUTO_RESYNCED)
        if is_unknown_verse_field_error(err2):
            return _unknown_field_after_resync(
                tool_name=tool_name,
                actor_path=actor_path,
                field=field,
                err=err2,
                raised=None,
                result=retried,
            )
        return retried

    if not is_stale_reflection_error(err):
        if raised is not None:
            raise raised
        return result

    try:
        payload = _compile_verse()
    except Exception as exc:  # noqa: BLE001 — 10054 = build started; else UEFN closed
        compile_err = str(exc)
        if is_build_started_error(compile_err):
            _reload_listener()
            return _wait_then_retry(
                call,
                tool_name=tool_name,
                actor_path=actor_path,
                field=field,
                err=err,
            )
        return {
            "ok": False,
            "tool": tool_name,
            "error": err,
            "compile_error": compile_err,
            "next": NEXT_OPEN_UEFN,
        }

    compile_info = payload.get("compile") if isinstance(payload.get("compile"), dict) else {}
    raw_compile = json.dumps(payload, default=str)
    num_errors = 0
    try:
        num_errors = int(compile_info.get("numErrors") or 0)
    except (TypeError, ValueError):
        num_errors = 0
    if num_errors > 0 and not is_build_started_error(raw_compile):
        return {
            "ok": False,
            "tool": tool_name,
            "error": err,
            "compile": compile_info,
            "next": NEXT_FIX_ERRORS,
        }

    _reload_listener()
    if is_build_started_error(raw_compile) or num_errors == 0:
        return _wait_then_retry(
            call,
            tool_name=tool_name,
            actor_path=actor_path,
            field=field,
            err=err,
        )
    return {
        "ok": False,
        "tool": tool_name,
        "error": err,
        "compile": compile_info,
        "next": NEXT_FIX_ERRORS,
    }
