"""Successful verification checks, isolated by conversation and agent run.

MCP requests may run in separate tasks, threads and bridge processes. A small
atomic receipt shares evidence for the same run without depending on a caller
ContextVar surviving between requests. Embedded turns reset their own scope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path
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

_scope: ContextVar[tuple[str, str]] = ContextVar("ducky_verify_scope", default=("", ""))
_lock = threading.Lock()
_evidence: dict[tuple[str, str], list[str]] = {}


def _current_scope() -> tuple[str, str]:
    from backend.workspace.identity import resolve_context

    ctx = resolve_context()
    if ctx is not None:
        if ctx.run_id:
            return (ctx.conv_id, ctx.run_id)
        # Only an explicitly bound embedded turn can use an empty run ID.
        # Anonymous external requests must never inherit earlier checks.
        embedded = _scope.get()
        return embedded if embedded[0] == ctx.conv_id else ("", "")
    return _scope.get()


def _receipt_path(scope: tuple[str, str]) -> Path:
    from frontend.app_paths import resolve_app_data_dir

    key = hashlib.sha256("\0".join(scope).encode()).hexdigest()
    return resolve_app_data_dir(for_write=True) / "verify-evidence" / (key + ".json")


def bind_conversation(conv_id: str, run_id: str = "") -> object:
    """Start an embedded turn; external MCP requests use their bound run identity."""
    scope = (str(conv_id or ""), str(run_id or ""))
    token = _scope.set(scope)
    with _lock:
        _evidence[scope] = []
        if all(scope):
            _receipt_path(scope).unlink(missing_ok=True)
    return token


def reset_conversation(token: object) -> None:
    scope = _scope.get()
    _scope.reset(token)  # type: ignore[arg-type]
    with _lock:
        _evidence.pop(scope, None)


def current_conversation() -> str:
    return _current_scope()[0]


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
    _record_key(key)


def _record_key(key: str) -> None:
    scope = _current_scope()
    if not scope[0]:
        return
    with _lock:
        if _scope.get() == scope:
            bucket = _evidence.setdefault(scope, [])
            if key not in bucket:
                bucket.append(key)
        if all(scope):
            path = _receipt_path(scope)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps({
                    "conv_id": scope[0], "run_id": scope[1], "tool": key,
                }), encoding="utf-8")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)


def evidence_names() -> list[str]:
    scope = _current_scope()
    if not scope[0]:
        return []
    with _lock:
        names = list(_evidence.get(scope, []))
        if all(scope):
            try:
                receipt = json.loads(_receipt_path(scope).read_text(encoding="utf-8"))
                if (receipt.get("conv_id"), receipt.get("run_id")) == scope:
                    tool = receipt.get("tool")
                    if (isinstance(tool, str) and tool not in names
                            and (is_check_tool(tool) or tool in _RESULT_CHECKS)):
                        names.append(tool)
            except (OSError, ValueError, AttributeError):
                pass
        return names


def has_evidence(chat_id: str = "") -> bool:
    if chat_id and chat_id != current_conversation():
        return False
    names = evidence_names()
    if any(n != OFFLINE_VERSE_CHECK for n in names):
        return True
    # The Problems-panel scan stands in for the compile only while the compile can't run.
    return OFFLINE_VERSE_CHECK in names and not verse_build_available()


def verse_build_available() -> bool:
    """True when workspace_compile_verse could run now: the Verse Workflow Server (UEFN)
    answers, probed the way the compile tool does. Never raises."""
    try:
        from frontend.ui_web.verse_editor.workflow.client import get_workflow_client

        client = get_workflow_client()
        if not client.connected:
            client.start(timeout_s=1.0)
        return bool(client.connected)
    except Exception:  # noqa: BLE001 — unreachable is the answer, not an error
        return False


# Checks that count only when their result says ok. Plugin teams could not tick any Verify
# step (Oct 12 2026): none of the plugin checks counted.
_RESULT_CHECKS = frozenset({"run_workflow", "ducky_terminal_run", "ducky_plugin_validate", "ducky_plugin_test",
                            "workspace_list_verse_errors"})
# A complete offline Verse scan (verse-lsp, no UEFN). It proves a Verify step only while
# UEFN is closed: then the compile can't run, and members were refused their tick, sent
# back to work, ended "BLOCKED: needs UEFN" and their lead cancelled a step whose done-when
# was met (Oct 22 2026, a Claude 5.5 team with UEFN closed).
OFFLINE_VERSE_CHECK = "workspace_list_verse_errors"


def _result_payloads(raw: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Read FastMCP content/structured tuples and protocol CallToolResult objects."""
    from backend.agent.serialization import parse_tool_result_envelope

    failed = False
    payloads: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        nonlocal failed
        if hasattr(value, "model_dump"):
            value = value.model_dump(by_alias=True, exclude_none=True)
        if isinstance(value, (tuple, list)):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("isError"):
                failed = True
            if "content" in value:
                visit(value["content"])
                visit(value.get("structuredContent"))
            elif value.get("type") == "text":
                visit(str(value.get("text") or ""))
            elif set(value) == {"result"}:
                # FastMCP wraps a string return in structuredContent.result.
                visit(value["result"])
            else:
                payloads.append(value)
        elif isinstance(value, str):
            if value.lstrip().lower().startswith(("error", "interrupted", "cancelled")):
                failed = True
            parsed = parse_tool_result_envelope(value)
            if parsed is not None:
                payloads.append(parsed)

    visit(raw)
    return failed, payloads


def _failed_payload(value: dict[str, Any]) -> bool:
    return (value.get("ok") is False or value.get("success") is False
            or bool(value.get("error")) or bool(value.get("stopped"))
            or bool(value.get("cancelled"))
            or value.get("exit_code") not in (None, 0))


def record_result(name: str, arguments: dict[str, Any] | None, raw: Any) -> None:
    """Record actual successful check results before tool output is compacted."""
    key = effective_tool_name(name, arguments)
    if not is_check_tool(name, arguments) and key not in _RESULT_CHECKS:
        return
    failed, payloads = _result_payloads(raw)
    if failed or any(_failed_payload(p) for p in payloads):
        return
    if key == OFFLINE_VERSE_CHECK:
        # Proof only when it checked every Verse file of an island (complete is False when it ran
        # out of time; a folder project has no Verse, so "no files" checked nothing) and found no
        # errors: a scan listing errors is the opposite of a passing check.
        if payloads and not any(
            p.get("complete") is False
            or p.get("folder_project")
            or any(int((f or {}).get("errors") or 0) > 0 for f in p.get("files") or [] if isinstance(f, dict))
            for p in payloads
        ):
            _record_key(key)
        return
    if key in _RESULT_CHECKS:
        # Starting background work or merely accepting a command is not proof.
        if not payloads or not all(p.get("ok") is True for p in payloads):
            return
        if key == "ducky_terminal_run":
            args = arguments or {}
            if args.get("wait") is False or args.get("background"):
                return
            if not all(p.get("exit_code") == 0 for p in payloads):
                return
        elif key == "run_workflow":
            for payload in payloads:
                steps = payload.get("steps")
                if (not isinstance(steps, list) or not steps
                        or any(not isinstance(s, dict) or s.get("ok") is not True
                               or _failed_payload(s) for s in steps)):
                    return
        _record_key(key)
    else:
        record_ok(name, arguments)


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


_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(?:restart|close|delete|remove|overwrite|reset|wipe|discard|revert|kill)\b"
)


def _is_destructive_question(text: str, start: int, end: int) -> bool:
    """Match sits in a question about a destructive step (asking first is correct)."""
    s = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    stops = [i for i in (text.find(c, end) for c in ".!?\n") if i >= 0]
    e = min(stops) if stops else len(text)
    return e < len(text) and text[e] == "?" and bool(_DESTRUCTIVE_RE.search(text[s:e]))


def handoff_warning(
    assistant_message: dict[str, Any] | None, *, asked_user: bool = False
) -> str | None:
    """Banner when the reply asks the user to finish / playtest / Build Verse.

    Silent when the turn asked through ducky_ask_user, or every match is a
    question about a destructive step.
    """
    if not assistant_message or asked_user:
        return None
    text = _message_text(assistant_message)
    if not any(
        not _is_destructive_question(text, m.start(), m.end())
        for m in _HANDOFF_RE.finditer(text)
    ):
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
