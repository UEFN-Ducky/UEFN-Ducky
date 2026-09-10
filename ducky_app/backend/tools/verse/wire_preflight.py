"""Wiring preflight: auto-compile + listener reload when Verse reflection is stale.

Host-side only (never runs inside the UEFN listener). A wire/set call that fails
because the Verse class was not built yet ("STALE REFLECTION", "no compiled
hash", "Verse class not found", …) is retried ONCE after
``workspace_compile_verse`` + ``reload_listener``. Anything else propagates
unchanged.

A field that is still stale after that one retry is locked: further wire_*
calls on the same device+field return immediately (no compile, no editor, no
ledger spam). Unlock when ``get_verse_editables`` shows a mangled hash.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Iterable

STALE_MARKERS: tuple[str, ...] = (
    "STALE REFLECTION",
    "no compiled hash",
    "Verse behavior not found",
    "Verse class not found",
    "not found under _Verse. Recompile",
)

AUTO_RECOVERED = "compiled + reloaded listener"
NEXT_FIX_ERRORS = "fix the Verse errors, then retry"
NEXT_OPEN_UEFN = "Open the project in UEFN and run workspace_compile_verse, then retry once"
NEXT_STALE_LOCKED = (
    "Hashes still missing after one compile+reload. Call get_verse_editables on "
    "THIS SAME device. Do NOT call wire_* again until mangled_name is set. "
    "Do not compile-loop. Do not place a second copy of the device."
)

_lock = threading.Lock()
# (actor_ident, field) → last error. actor_ident is a lowercase label or path tail.
_stale_fields: dict[tuple[str, str], str] = {}


def is_stale_reflection_error(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(marker.lower() in low for marker in STALE_MARKERS)


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


def run_with_build_retry(
    call: Callable[[], Any],
    *,
    tool_name: str,
    actor_path: str = "",
    field: str = "",
) -> Any:
    """Call *call*; on a stale-reflection failure compile Verse, reload the listener, retry once.

    Returns whatever ``call()`` returns. A recovered retry result carries
    ``auto_recovered`` = ``"compiled + reloaded listener"``. Non-stale failures
    are returned / re-raised unchanged.

    A field that stays stale after that retry is locked so the next wire_* on
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
    if not is_stale_reflection_error(err):
        if raised is not None:
            raise raised
        return result

    try:
        payload = _compile_verse()
    except Exception as exc:  # noqa: BLE001 — Workflow Server not connected / UEFN closed
        return {
            "ok": False,
            "tool": tool_name,
            "error": err,
            "compile_error": str(exc),
            "next": NEXT_OPEN_UEFN,
        }

    compile_info = payload.get("compile") if isinstance(payload.get("compile"), dict) else {}
    num_errors = 0
    try:
        num_errors = int(compile_info.get("numErrors") or 0)
    except (TypeError, ValueError):
        num_errors = 0
    if num_errors > 0:
        return {
            "ok": False,
            "tool": tool_name,
            "error": err,
            "compile": compile_info,
            "next": NEXT_FIX_ERRORS,
        }

    _reload_listener()
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
    return add_field(retried, "auto_recovered", AUTO_RECOVERED)
