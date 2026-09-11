"""Full-text search across Content .verse files and project duckies."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from frontend.ui_web.project_chats import list_conversations
from frontend.ui_web.project_files import (
    _content_dir,
    _project_root,
    include_in_workspace_search,
)

SearchScope = Literal["files", "chats", "both"]


def _tokens(query: str) -> list[str]:
    return [t.lower() for t in re.split(r"[\s_/.\-]+", (query or "").strip()) if t]


def _fold(text: str) -> str:
    return re.sub(r"[\s_/.\-]+", " ", (text or "").lower())


def tokens_match(query: str, *parts: str) -> bool:
    """True when every query token appears in the folded haystack (spaces = _)."""
    toks = _tokens(query)
    if not toks:
        return False
    hay = _fold(" ".join(parts))
    return all(t in hay for t in toks)


def _compile_matcher(query: str, *, case_sensitive: bool, whole_word: bool) -> re.Pattern[str]:
    escaped = re.escape(query.strip())
    if whole_word:
        escaped = rf"\b{escaped}\b"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(escaped, flags)


def _preview(text: str, match: re.Match[str], *, max_len: int = 140) -> str:
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 60)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    if len(snippet) > max_len:
        snippet = snippet[: max_len - 1] + "…"
    return snippet


def _search_text_lines(
    text: str,
    matcher: re.Pattern[str],
    *,
    max_matches: int,
) -> list[dict[str, int | str]]:
    hits: list[dict[str, int | str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in matcher.finditer(line):
            hits.append(
                {
                    "line": line_no,
                    "column": match.start() + 1,
                    "preview": _preview(line, match),
                }
            )
            if len(hits) >= max_matches:
                return hits
    return hits


def _search_plain_text(
    text: str,
    matcher: re.Pattern[str],
    *,
    max_matches: int,
    message_id: int | str,
) -> list[dict[str, int | str]]:
    hits: list[dict[str, int | str]] = []
    for match in matcher.finditer(text):
        hits.append(
            {
                "message_id": message_id,
                "preview": _preview(text.replace("\n", " "), match),
            }
        )
        if len(hits) >= max_matches:
            break
    return hits


def _walk_verse_files(content: Path, root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []

    def walk(dir_path: Path) -> None:
        try:
            names = sorted(os.listdir(dir_path), key=lambda s: s.lower())
        except OSError:
            return
        for name in names:
            full = dir_path / name
            is_dir = full.is_dir()
            if not include_in_workspace_search(name, is_dir):
                continue
            if is_dir:
                walk(full)
            else:
                rel = str(full.relative_to(root)).replace("\\", "/")
                found.append((rel, full))

    walk(content)
    return found


def _search_files(
    query: str,
    matcher: re.Pattern[str],
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], int]:
    root = _project_root().resolve()
    content = _content_dir()
    file_results: list[dict[str, Any]] = []
    total_matches = 0
    remaining = max_results

    for rel_path, full_path in _walk_verse_files(content, root):
        if remaining <= 0:
            break
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = _search_text_lines(text, matcher, max_matches=remaining)
        if not matches and tokens_match(query, rel_path):
            matches = [{"line": 1, "column": 1, "preview": rel_path}]
        if not matches:
            continue
        file_results.append({"path": rel_path, "matches": matches})
        total_matches += len(matches)
        remaining -= len(matches)

    return file_results, total_matches


def _message_search_text(message: dict[str, Any]) -> str:
    role = message.get("role", "")
    if role == "user":
        return str(message.get("text") or message.get("content") or "")
    if role == "assistant":
        parts = [str(message.get("content") or "")]
        for block in message.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            name = block.get("name", "")
            args = block.get("arguments") or {}
            parts.append(f"{name} {args}")
            result = block.get("result")
            if isinstance(result, dict):
                parts.append(str(result.get("text") or result.get("hint") or ""))
        return " ".join(p for p in parts if p)
    return str(message.get("text") or message.get("content") or "")


def _search_chats_rows(
    query: str,
    matcher: re.Pattern[str],
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], int]:
    """ADR 0003: titles from the conversations table, bodies from FTS5.

    The file-store version iterated ``conv.messages`` on objects that the list
    call had stripped, so body search silently matched titles only.
    """
    from frontend.ui_web.project_chats import _project_id, _repo

    repo = _repo()
    project_id = _project_id()
    by_conv: dict[str, dict[str, Any]] = {}
    total = 0
    for conv in list_conversations(folder_id=None):
        title = conv.title or ""
        ducky = str(getattr(conv, "ducky_name", "") or "")
        personality = str(getattr(conv, "ducky_personality", "") or "")
        summary = str(getattr(conv, "context_summary", "") or "")
        hit = matcher.search(title) or matcher.search(ducky)
        if hit or tokens_match(query, title, ducky, personality, summary):
            snippet = title if matcher.search(title) else (ducky or summary or personality or title)
            preview = _preview(snippet, hit) if hit else snippet[:140]
            by_conv[conv.id] = {
                "id": conv.id,
                "title": conv.title,
                "folder_id": conv.folder_id or "",
                "ducky_style": conv.ducky_style or "",
                "ducky_name": ducky,
                "matches": [{"message_id": "title", "preview": preview}],
            }
            total += 1
    meta = {c.id: c for c in list_conversations(folder_id=None)}
    for row in repo.search_messages(project_id, query, limit=max_results):
        if total >= max_results:
            break
        conv = meta.get(row["conv_id"])
        if conv is None:
            continue
        entry = by_conv.setdefault(
            conv.id,
            {
                "id": conv.id,
                "title": conv.title,
                "folder_id": conv.folder_id or "",
                "ducky_style": conv.ducky_style or "",
                "matches": [],
            },
        )
        entry["matches"].append({"message_id": int(row["seq"]), "preview": str(row["preview"] or "")})
        total += 1
    return list(by_conv.values()), total


def _search_chats(
    query: str,
    matcher: re.Pattern[str],
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], int]:
    chat_results: list[dict[str, Any]] = []
    total_matches = 0
    remaining = max_results

    from backend.store.switch import use_db

    if use_db("chats"):
        return _search_chats_rows(query, matcher, max_results=max_results)

    for conv in list_conversations(folder_id=None):
        if remaining <= 0:
            break
        matches: list[dict[str, int | str]] = []
        title = conv.title or ""
        title_match = matcher.search(title)
        if title_match:
            matches.append(
                {
                    "message_id": "title",
                    "preview": _preview(title, title_match),
                }
            )
        for idx, message in enumerate(conv.messages):
            if remaining <= 0:
                break
            if not isinstance(message, dict):
                continue
            text = _message_search_text(message)
            if not text:
                continue
            for hit in _search_plain_text(text, matcher, max_matches=remaining, message_id=idx):
                matches.append(hit)
                remaining -= 1
                if remaining <= 0:
                    break
        if not matches:
            continue
        chat_results.append(
            {
                "id": conv.id,
                "title": conv.title,
                "folder_id": conv.folder_id or "",
                "ducky_style": conv.ducky_style or "",
                "matches": matches,
            }
        )
        total_matches += len(matches)

    return chat_results, total_matches


def _search_memory(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from backend.store.switch import use_db
    from frontend.ui_web.project_chats import _project_id

    if not use_db("memory"):
        return []
    try:
        from backend.store.repos import memory as memory_repo

        return [
            {
                "name": str(row.get("name") or ""),
                "preview": str(row.get("preview") or row.get("description") or ""),
            }
            for row in memory_repo.search(_project_id(), query, limit=max_results)
        ]
    except Exception:
        return []


def _search_ledger(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from backend.store.switch import use_db
    from frontend.ui_web.project_chats import _project_id

    if not use_db("ledger"):
        return []
    try:
        from backend.store import db

        pid = _project_id()
        rows = db.connect().execute(
            "SELECT path FROM path_index WHERE project_id=?", (pid,)
        ).fetchall()
        extra = db.connect().execute(
            "SELECT DISTINCT e.path FROM run_entries e JOIN runs r ON r.run_id = e.run_id "
            "WHERE r.project_id=? AND e.path <> ''",
            (pid,),
        ).fetchall()
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for (path,) in list(rows) + list(extra):
        p = str(path or "")
        if not p or p in seen or not tokens_match(query, p):
            continue
        seen.add(p)
        hits.append({"path": p, "preview": p})
        if len(hits) >= max_results:
            break
    return hits


def _search_history(query: str, *, max_results: int) -> list[dict[str, Any]]:
    from backend.store.switch import use_db
    from frontend.ui_web.project_chats import _project_id

    if not use_db("ledger"):
        return []
    try:
        from backend.store import db

        rows = db.connect().execute(
            "SELECT path, preview, ducky_name FROM file_versions WHERE project_id=? "
            "ORDER BY saved_at DESC LIMIT 400",
            (_project_id(),),
        ).fetchall()
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, preview, ducky_name in rows:
        p = str(path or "")
        if not p or p in seen:
            continue
        if not tokens_match(query, p, str(preview or ""), str(ducky_name or "")):
            continue
        seen.add(p)
        hits.append({"path": p, "preview": str(preview or p), "ducky_name": str(ducky_name or "")})
        if len(hits) >= max_results:
            break
    return hits


def search_workspace(
    query: str,
    *,
    scope: SearchScope = "both",
    case_sensitive: bool = False,
    whole_word: bool = False,
    max_results: int = 500,
) -> dict[str, Any]:
    cleaned = (query or "").strip()
    if not cleaned:
        return {
            "query": "",
            "scope": scope,
            "file_results": [],
            "chat_results": [],
            "memory_results": [],
            "ledger_results": [],
            "history_results": [],
            "stats": {
                "file_count": 0,
                "file_match_count": 0,
                "chat_count": 0,
                "chat_match_count": 0,
                "memory_count": 0,
                "ledger_count": 0,
                "history_count": 0,
            },
        }

    matcher = _compile_matcher(cleaned, case_sensitive=case_sensitive, whole_word=whole_word)
    per_scope_budget = max_results if scope != "both" else max(max_results // 2, 1)

    file_results: list[dict[str, Any]] = []
    chat_results: list[dict[str, Any]] = []
    file_match_count = 0
    chat_match_count = 0

    if scope in ("files", "both"):
        file_results, file_match_count = _search_files(cleaned, matcher, max_results=per_scope_budget)
    if scope in ("chats", "both"):
        chat_results, chat_match_count = _search_chats(cleaned, matcher, max_results=per_scope_budget)
    extra_budget = max(12, per_scope_budget // 4)
    memory_results = _search_memory(cleaned, max_results=extra_budget) if scope in ("chats", "both") else []
    ledger_results = _search_ledger(cleaned, max_results=extra_budget) if scope in ("files", "both") else []
    history_results = _search_history(cleaned, max_results=extra_budget) if scope in ("files", "both") else []

    return {
        "query": cleaned,
        "scope": scope,
        "file_results": file_results,
        "chat_results": chat_results,
        "memory_results": memory_results,
        "ledger_results": ledger_results,
        "history_results": history_results,
        "stats": {
            "file_count": len(file_results),
            "file_match_count": file_match_count,
            "chat_count": len(chat_results),
            "chat_match_count": chat_match_count,
            "memory_count": len(memory_results),
            "ledger_count": len(ledger_results),
            "history_count": len(history_results),
        },
    }


def _replace_files(matcher: re.Pattern[str], replacement: str) -> tuple[int, int]:
    """Write replacements into Content .verse files only."""
    from frontend.ui_web.verse_editor.io import write_file

    root = _project_root().resolve()
    content = _content_dir()
    file_count = 0
    match_count = 0

    for rel_path, full_path in _walk_verse_files(content, root):
        if full_path.suffix.lower() != ".verse":
            continue
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_text, n = matcher.subn(replacement, text)
        if not n:
            continue
        write_file(rel_path, new_text)
        file_count += 1
        match_count += n

    return file_count, match_count


def replace_workspace(
    query: str,
    replacement: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
) -> dict[str, Any]:
    """Replace matches in Content .verse files only.

    Duckies and conversation history are immutable — this API never reads or
    writes chat data.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {
            "query": "",
            "replacement": replacement,
            "scope": "files",
            "stats": {
                "file_count": 0,
                "file_match_count": 0,
                "chat_count": 0,
                "chat_match_count": 0,
            },
        }

    matcher = _compile_matcher(cleaned, case_sensitive=case_sensitive, whole_word=whole_word)
    file_count, file_match_count = _replace_files(matcher, replacement)

    return {
        "query": cleaned,
        "replacement": replacement,
        "scope": "files",
        "stats": {
            "file_count": file_count,
            "file_match_count": file_match_count,
            "chat_count": 0,
            "chat_match_count": 0,
        },
    }
