"""Stored conversation → flat chat rows for the web UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _tool_result_text(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    if result.get("data"):
        return str(result["data"])
    if result.get("error"):
        return str(result["error"])
    return json.dumps(result, ensure_ascii=False)


# Row id = message index * ROW_ID_STEP + row index within that message, so a
# page knows its ids without converting the messages before it.
# UI ids are transient, never message database keys. Leave room for very large
# single turns; exact JS integers up to two million stored messages.
ROW_ID_STEP = 2**32


def _messages_to_ui(
    conv,
    project_root: str | None = None,
    *,
    live: bool = False,
    before_id: int | None = None,
    limit: int | None = None,
    message_offset: int = 0,
    last_message_index: int | None = None,
    display_attachments: bool = False,
    conversations_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Stored conversation → flat UI rows.

    ``live``: the conversation's run is still going. The checkpoint with a
    matching live run id is a turn in progress, not an
    interrupted one: no "Interrupted" flag, and tool calls without a result yet stay
    running instead of showing as failed ``· pending ·`` rows.

    ``limit`` / ``before_id``: only the ``limit`` newest rows (before ``before_id``),
    converting messages from the end until enough rows exist.
    """
    from frontend.settings import PanelSettings
    from frontend.ui_web.conversation_attachments import hydrate_attachment_dicts
    from frontend.ui_web.chats.store import get_conversations_dir

    root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
    conv_dir = conversations_dir if conversations_dir is not None else get_conversations_dir(root)
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    live_runs = get_live_run_ids() if live else frozenset()
    last_index = message_offset + len(conv.messages) - 1 if last_message_index is None else last_message_index

    def convert(index: int, m: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        i = index * ROW_ID_STEP
        role = m.get("role", "user")
        if role == "user":
            row: dict[str, Any] = {"id": i, "role": "user", "text": m.get("text", m.get("content", ""))}
            attachments = m.get("attachments")
            if attachments:
                row["attachments"] = hydrate_attachment_dicts(attachments, conv.id, conv_dir, root, display=display_attachments)
            out.append(row)
            return out
        if role == "assistant":
            in_flight = (
                live
                and bool(m.get("incomplete"))
                and (m.get("run_id") in live_runs if m.get("run_id") else index == last_index)
            )
            blocks = m.get("blocks") or []
            # Count cheap shell fields first, so a page inside a huge turn need
            # not inflate or convert every old tool result. Keep absolute ids.
            widths = [
                (1 if in_flight and b.get("status") == "pending" else 2) if b.get("type") == "tool_call"
                else int(bool(str(b.get("text") or "").strip())) if b.get("type") in ("text", "thinking", "user")
                else 0 for b in blocks
            ]
            tail = bool(m.get("content") or m.get("attachments") or str(m.get("thinking") or "").strip()
                        or m.get("incomplete"))
            upper = index * ROW_ID_STEP + sum(widths) + int(tail)
            if before_id is not None:
                upper = min(upper, before_id)
            lower = upper - int(limit) if limit is not None and int(limit) > 0 else index * ROW_ID_STEP
            for block, width in zip(blocks, widths):
                if i + width <= lower or i >= upper:
                    i += width
                    continue
                btype = block.get("type")
                if btype == "thinking":
                    # Per-step reasoning, shown inline between tools (Cursor-style).
                    seg = str(block.get("text") or "")
                    if seg.strip():
                        out.append({"id": i, "role": "assistant", "text": "", "thinking": seg})
                        i += 1
                    continue
                if btype == "text":
                    # Narration the model spoke between tool calls — kept in order.
                    seg = str(block.get("text") or "")
                    if seg.strip():
                        out.append({"id": i, "role": "assistant", "text": seg})
                        i += 1
                    continue
                if btype == "user":
                    # A message the person sent into this turn (steer.py), where it landed.
                    seg = str(block.get("text") or "")
                    if seg.strip():
                        out.append({"id": i, "role": "user", "text": seg, "steered": True})
                        i += 1
                    continue
                if btype != "tool_call":
                    continue
                name = block.get("name", "?")
                args = block.get("arguments") or {}
                args_short = json.dumps(args, ensure_ascii=False)
                if len(args_short) > 120:
                    args_short = args_short[:117] + "..."
                tool_line = f"⚙ {name}({args_short})"
                out.append(
                    {
                        "id": i,
                        "role": "tool",
                        "text": tool_line,
                        "tool": {
                            "id": str(block.get("id") or ""),
                            "name": name,
                            "arguments": args,
                            "status": "pending",
                        },
                    }
                )
                i += 1
                status = block.get("status", "?")
                if in_flight and status == "pending":
                    continue  # still running: the tool row above shows it in progress
                ms = int(block.get("duration_ms", 0) or 0)
                done_role = "success" if status == "success" else "error"
                block_result = block.get("result") if isinstance(block.get("result"), dict) else {}
                tool_done: dict[str, Any] = {
                    "id": str(block.get("id") or ""),
                    "name": name,
                    "arguments": args,
                    "status": status,
                    "durationMs": ms,
                    "result": _tool_result_text(block_result),
                    "hint": str(block_result.get("hint") or ""),
                }
                exit_code = block_result.get("exit_code", block.get("exit_code"))
                if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                    tool_done["exitCode"] = exit_code
                llm_tokens_val = block.get("llm_tokens")
                if isinstance(llm_tokens_val, int) and llm_tokens_val > 0:
                    tool_done["llmTokens"] = llm_tokens_val
                stored_edit = block.get("file_edit")
                stored_edits = block.get("file_edits")
                if isinstance(stored_edits, list):
                    tool_done["fileEdits"] = stored_edits
                    if stored_edits:
                        tool_done["fileEdit"] = stored_edits[0]
                elif isinstance(stored_edit, dict):
                    tool_done["fileEdit"] = stored_edit
                else:
                    try:
                        from frontend.ui_web.verse_editor.agent_sync import build_file_edit_meta

                        file_edit = build_file_edit_meta(name, args, block_result)
                        if file_edit:
                            tool_done["fileEdit"] = file_edit
                    except Exception:
                        pass
                out.append(
                    {
                        "id": i,
                        "role": done_role,
                        "text": f"⚙ {tool_line[2:]} · {status} · {ms}ms",
                        "tool": tool_done,
                    }
                )
                i += 1
            content = m.get("content", "")
            thinking = m.get("thinking")
            author = m.get("author") if isinstance(m.get("author"), dict) else None
            pictures = m.get("attachments") if isinstance(m.get("attachments"), list) else []
            if content or pictures or (isinstance(thinking, str) and thinking.strip()):
                row_asst: dict[str, Any] = {"id": i, "role": "assistant", "text": content}
                if pictures:
                    # Pictures the agent made this turn show under its reply.
                    row_asst["attachments"] = hydrate_attachment_dicts(pictures, conv.id, conv_dir, root, display=display_attachments)
                if isinstance(thinking, str) and thinking.strip():
                    row_asst["thinking"] = thinking
                if author:
                    row_asst["author"] = {
                        "name": str(author.get("name") or "").strip(),
                        "member_conv_id": str(author.get("member_conv_id") or "").strip(),
                        "tts_voice": str(author.get("tts_voice") or "").strip(),
                        "tts_speed": float(author.get("tts_speed") or 0.0),
                        "color": str(author.get("color") or "").strip(),
                        "profile_id": str(author.get("profile_id") or "").strip(),
                    }
                tours = m.get("tours")
                if isinstance(tours, list) and tours:
                    # Walkthroughs this message offers as buttons (the welcome ducky).
                    row_asst["tours"] = [str(t) for t in tours if str(t).strip()]
                if m.get("incomplete") and not in_flight:
                    row_asst["incomplete"] = True
                    if isinstance(m.get("error"), str) and m["error"].strip():
                        row_asst["error"] = m["error"]
                if m.get("incomplete"):
                    row_asst["checkpoint"] = True
                    row_asst["run_id"] = m.get("run_id")
                    row_asst["live_run"] = in_flight
                out.append(row_asst)
                i += 1
            elif m.get("incomplete"):
                # Crashed turn whose final step produced no answer/reasoning of
                # its own (it's all in the interleaved blocks above) — still show
                # the interruption so it never silently disappears.
                err = m.get("error")
                row_inc: dict[str, Any] = {
                    "id": i,
                    "role": "assistant",
                    "text": "",
                    "incomplete": not in_flight,
                    "checkpoint": True,
                    "run_id": m.get("run_id"),
                    "live_run": in_flight,
                    **({"error": err} if isinstance(err, str) and err.strip() else {}),
                }
                if author:
                    row_inc["author"] = {
                        "name": str(author.get("name") or "").strip(),
                        "member_conv_id": str(author.get("member_conv_id") or "").strip(),
                        "tts_voice": str(author.get("tts_voice") or "").strip(),
                        "tts_speed": float(author.get("tts_speed") or 0.0),
                        "color": str(author.get("color") or "").strip(),
                        "profile_id": str(author.get("profile_id") or "").strip(),
                    }
                out.append(row_inc)
        return out

    messages = conv.messages
    if before_id is None and limit is None:
        return [row for index, m in enumerate(messages, message_offset) for row in convert(index, m)]
    end = len(messages) if before_id is None else min(len(messages), int(before_id) // ROW_ID_STEP + 1 - message_offset)
    want = int(limit) if limit is not None and int(limit) > 0 else None
    pages: list[list[dict[str, Any]]] = []
    count = 0
    for index in range(end - 1, -1, -1):
        rows = convert(index + message_offset, messages[index])
        if before_id is not None:
            rows = [r for r in rows if r["id"] < int(before_id)]
        pages.append(rows)
        count += len(rows)
        if want is not None and count >= want:
            break
    rows = [r for page in reversed(pages) for r in page]
    return rows[-want:] if want is not None else rows
