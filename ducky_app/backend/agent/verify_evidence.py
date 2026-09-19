"""Per-conversation verify-evidence ledger (embedded agent only).

ponytail: in-process dict keyed by conv_id, reset each runner turn. Ceiling is
one process / one turn — persist on the changeset journal if verify must
survive a restart.
"""

from __future__ import annotations

import re
import threading
from contextvars import ContextVar
from typing import Any

from backend.agent.toolsets import effective_tool_name

# Domain → check tool (hard_rules Evidence of done). Inner names for
# ducky_call_tool / unreal__call_tool count the same as the floor name.
CHECK_TOOLS = frozenset(
    {
        "workspace_compile_verse",
        "get_verse_editables",
        "inspect_verse_device",
        "device_graph_audit",
        "GetDeviceProperties",
        "take_high_res_screenshot",
        "verse_test_run",
        "verse_test_results",
        "tester_get_results",
        "validate_uefn_asset",
        "changeset_list",
        "get_material_info",
        "get_static_mesh_info",
        "get_asset_info",
        "get_niagara_system_info",
        "get_skeletal_mesh_info",
        "get_widget_blueprint_info",
        "get_data_table_info",
        "code_list_errors",
    }
)

_CLAIM_RE = re.compile(
    r"(?is)\b("
    r"verified|verification passed|compiles\b|compiled successfully"
    r"|wired\b|wiring (?:is )?done|build succeeded|tests? pass(?:ed)?"
    r"|\bPASS\b|errors? (?:are )?fixed|done when is met"
    r")\b"
)

_HANDOFF_RE = re.compile(
    r"(?is)("
    r"run .{0,40}to check|confirm it compiles|tell me to continue"
    r"|playtest and|build Verse|drag (?:it )?in Details|restart UEFN"
    r"|you can verify|once you (?:build|playtest|confirm)"
    r")"
)

_conv_id: ContextVar[str] = ContextVar("ducky_verify_conv_id", default="")
_lock = threading.Lock()
# conv_id -> check-tool names that returned ok this turn
_evidence: dict[str, list[str]] = {}


def bind_conversation(conv_id: str) -> object:
    """Bind this turn and clear leftover evidence. Returns a reset token."""
    cid = str(conv_id or "")
    token = _conv_id.set(cid)
    with _lock:
        _evidence[cid] = []
    return token


def reset_conversation(token: object) -> None:
    _conv_id.reset(token)  # type: ignore[arg-type]


def current_conversation() -> str:
    return _conv_id.get()


def is_check_tool(name: str, arguments: dict[str, Any] | None = None) -> bool:
    n = effective_tool_name(name, arguments)
    if n in CHECK_TOOLS:
        return True
    args = arguments if isinstance(arguments, dict) else {}
    if n == "unreal__call_tool" or str(name or "").strip() == "unreal__call_tool":
        inner = str(args.get("tool_name") or "").strip()
        leaf = inner.rsplit(".", 1)[-1]
        if inner in CHECK_TOOLS or leaf in CHECK_TOOLS:
            return True
    leaf = n.rsplit("__", 1)[-1] if "__" in n else n
    return leaf in CHECK_TOOLS


def record_ok(name: str, arguments: dict[str, Any] | None = None) -> None:
    if not is_check_tool(name, arguments):
        return
    key = effective_tool_name(name, arguments)
    args = arguments if isinstance(arguments, dict) else {}
    if key == "unreal__call_tool":
        inner = str(args.get("tool_name") or "").strip()
        if inner:
            key = inner.rsplit(".", 1)[-1]
    conv = _conv_id.get()
    with _lock:
        bucket = _evidence.setdefault(conv, [])
        if key not in bucket:
            bucket.append(key)


def evidence_names() -> list[str]:
    with _lock:
        return list(_evidence.get(_conv_id.get(), []))


def has_evidence() -> bool:
    return bool(evidence_names())


def _message_text(assistant_message: dict[str, Any]) -> str:
    parts = [str(assistant_message.get("content") or "")]
    for block in assistant_message.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or block.get("content") or ""))
    return "\n".join(parts)


def fake_verify_warning(assistant_message: dict[str, Any] | None) -> str | None:
    """Banner when the reply claims verified/compiles/wired/PASS with no check tool."""
    if not assistant_message:
        return None
    text = _message_text(assistant_message)
    if not _CLAIM_RE.search(text):
        return None
    if has_evidence():
        return None
    return (
        "This reply claims the work is verified / compiled / wired / PASS, but no "
        "check tool returned ok this turn. Run workspace_compile_verse, "
        "get_verse_editables, GetDeviceProperties, verse_test_run, "
        "validate_uefn_asset, take_high_res_screenshot, or changeset_list first."
    )


def handoff_warning(assistant_message: dict[str, Any] | None) -> str | None:
    """Banner when the reply asks the user to finish / playtest / Build Verse."""
    if not assistant_message:
        return None
    text = _message_text(assistant_message)
    if not _HANDOFF_RE.search(text):
        return None
    return (
        "This reply hands work back to the user (check / compile / continue / "
        "playtest / Build Verse / Details / restart). Finish the turn yourself — "
        "run the check tool, or call ducky_ask_user only for a real fork."
    )


def append_verify_warning(
    assistant_message: dict[str, Any],
    warning: str,
    *,
    title: str = "Not verified",
) -> dict[str, Any]:
    msg = dict(assistant_message)
    content = str(msg.get("content") or "").rstrip()
    banner = f"\n\n---\n\n⚠ **{title}:** {warning}"
    msg["content"] = (content + banner) if content else f"⚠ **{title}:** {warning}"
    warnings = list(msg.get("verify_warnings") or [])
    warnings.append(warning)
    msg["verify_warnings"] = warnings
    return msg
