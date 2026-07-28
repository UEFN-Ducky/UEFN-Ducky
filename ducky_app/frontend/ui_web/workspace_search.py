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


def _search_chats(
    query: str,
    matcher: re.Pattern[str],
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], int]:
    chat_results: list[dict[str, Any]] = []
    total_matches = 0
    remaining = max_results

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
            "stats": {
                "file_count": 0,
                "file_match_count": 0,
                "chat_count": 0,
                "chat_match_count": 0,
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

    return {
        "query": cleaned,
        "scope": scope,
        "file_results": file_results,
        "chat_results": chat_results,
        "stats": {
            "file_count": len(file_results),
            "file_match_count": file_match_count,
            "chat_count": len(chat_results),
            "chat_match_count": chat_match_count,
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
