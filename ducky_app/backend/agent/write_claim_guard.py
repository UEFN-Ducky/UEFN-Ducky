"""Detect replies that claim project files were written without a successful write tool."""

from __future__ import annotations

import re
from typing import Any

from backend.agent.toolsets import effective_tool_name

WRITE_TOOLS = frozenset(
    {
        "workspace_write_file",
        "create_project_verse_file",
    }
)

_CLAIM_RE = re.compile(
    r"(?is)("
    r"files were written|the files were written|were written!"
    r"|sits? in your project|you can verify"
    r"|##\s*Inventory"
    r"|\bI (?:wrote|created|added|saved)\b"
    r"|\b(?:wrote|created|added) the (?:file|device|verse)\b"
    r")"
)

_PATH_RE = re.compile(
    r"(?:Content/)?Verse/[A-Za-z0-9_./-]+\.verse"
    r"|`((?:Content/)?(?:Verse/)?[A-Za-z0-9_./-]+\.verse)`"
    r"|\]\(((?:Content/)?Verse/[A-Za-z0-9_./-]+\.verse)\)"
)


def normalize_rel(path: str) -> str:
    p = (path or "").strip().replace("\\", "/").lstrip("/")
    if p.lower().startswith("content/"):
        p = p[8:]
    return p.lower()


def write_path_from_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """relative_path from a write tool or ducky_call_tool wrapping one."""
    args = arguments if isinstance(arguments, dict) else {}
    inner = effective_tool_name(name, args)
    if inner not in WRITE_TOOLS:
        return ""
    if str(name or "").strip() == "ducky_call_tool" and isinstance(args.get("arguments"), dict):
        args = args["arguments"]
    return str(args.get("relative_path") or args.get("path") or "").strip()


def record_write_from_tool(
    rec: Any,
    written: set[str],
    failures: list[str],
) -> None:
    name = str(getattr(rec, "name", "") or "")
    args = getattr(rec, "arguments", None)
    if not isinstance(args, dict):
        args = {}
    rel = write_path_from_tool(name, args)
    if not rel:
        return
    status = str(getattr(rec, "status", "") or "")
    result = getattr(rec, "result", None)
    ok = status == "success" or (isinstance(result, dict) and result.get("ok") is True)
    if ok:
        written.add(rel)
    else:
        failures.append(rel)


def claimed_paths(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(text or ""):
        raw = (m.group(1) or m.group(2) or m.group(0) or "").strip()
        key = normalize_rel(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(raw.replace("\\", "/"))
    return found


def _message_text(assistant_message: dict[str, Any]) -> str:
    parts = [str(assistant_message.get("content") or "")]
    for block in assistant_message.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or block.get("content") or ""))
    return "\n".join(parts)


def fake_write_warning(
    assistant_message: dict[str, Any] | None,
    written: set[str],
    failures: list[str] | None = None,
) -> str | None:
    """User-visible warning when the reply invents writes this turn did not land."""
    if not assistant_message:
        return None
    text = _message_text(assistant_message)
    if not _CLAIM_RE.search(text):
        return None
    written_n = {normalize_rel(p) for p in written if p}
    claimed = claimed_paths(text)
    missing = [p for p in claimed if normalize_rel(p) not in written_n]
    if claimed and not missing:
        return None
    if not claimed and written_n:
        return None
    wrote = ", ".join(sorted(written)) if written else "(nothing)"
    failed = ", ".join(failures or [])
    if missing:
        detail = f"Listed but not written this turn: {', '.join(missing)}. "
    else:
        detail = "This reply says files were written, but no write tool succeeded. "
    fail_bit = f"Write tools failed for: {failed}. " if failed else ""
    return (
        f"{detail}{fail_bit}This turn actually wrote: {wrote}. "
        "Say what broke — do not invent files."
    )


def append_write_claim_warning(assistant_message: dict[str, Any], warning: str) -> dict[str, Any]:
    msg = dict(assistant_message)
    content = str(msg.get("content") or "").rstrip()
    banner = f"\n\n---\n\n⚠ **Files not written:** {warning}"
    msg["content"] = (content + banner) if content else f"⚠ **Files not written:** {warning}"
    msg["write_claim_warning"] = warning
    return msg
