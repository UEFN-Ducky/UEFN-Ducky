"""Per-project agent memory — named entry files shared by the project's duckies.

Skills-style layout, stored in APP DATA keyed by project slug (same separation
scheme as chats). A memory entry works like a skill pack:

    memory/projects/<proj-slug>/
      device-naming.md            # simple entry (one fact/topic)
      coding-standards/           # split entry — grew like a skill
        MEMORY.md                 #   main body / index of the topic
        error-handling.md         #   sub-entry, pulled individually
        naming.md

Only the INDEX (name + description lines, subs included) rides in prompts;
bodies are pulled on demand via project_memory_get with ``entry`` or
``entry/sub`` names. Every ducky WRITES only to its own project's memory but
can READ any project's via the ``project`` argument on the read tools. Living
in app data, entries survive deletion of the project folder itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir
from backend.util.env_compat import env_str

_MAX_ENTRY_CHARS = 16_000
_MAX_DESCRIPTION_CHARS = 140
_MAX_SLUG_CHARS = 60

_MAIN_FILE = "MEMORY.md"


def resolve_project_root(explicit: str = "") -> str:
    return (explicit or env_str("PROJECT_ROOT")).strip()


def memory_dir(project_root: str = "", *, for_write: bool = False) -> Path:
    # Same per-project keying as chats ("_no_project" when no root is set); the
    # slug embeds a path hash, so same-named projects can never share a folder.
    from frontend.ui_web.project_chats import project_slug

    root = resolve_project_root(project_root)
    d = default_app_data_dir() / "memory" / "projects" / project_slug(root)
    if for_write:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify_part(part: str) -> str:
    raw = (part or "").strip().lower()
    slug = "".join(c if c.isalnum() else "-" for c in raw)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")[:_MAX_SLUG_CHARS].strip("-")
    if not slug:
        raise ValueError(f"Invalid entry name: {part!r} (needs at least one letter or digit)")
    return slug


def slugify_entry_name(name: str) -> str:
    """``entry`` or ``entry/sub`` — each part slugged; deeper nesting is rejected."""
    parts = [p for p in (name or "").replace("\\", "/").split("/") if p.strip()]
    if not parts:
        raise ValueError(f"Invalid entry name: {name!r}")
    if len(parts) > 2:
        raise ValueError(
            f"Invalid entry name: {name!r} — one nesting level only (entry or entry/sub, like skills)"
        )
    return "/".join(_slugify_part(p) for p in parts)


def _split_slug(slug: str) -> tuple[str, str]:
    """→ (entry, sub); sub is '' for a top-level entry."""
    entry, _, sub = slug.partition("/")
    return entry, sub


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _entry_file(entry: str, project_root: str) -> Path:
    return memory_dir(project_root) / f"{entry}.md"


def _entry_dir(entry: str, project_root: str) -> Path:
    return memory_dir(project_root) / entry


def _main_path(entry: str, project_root: str) -> Path | None:
    """Where the entry's main body lives (flat file or dir/MEMORY.md), if it exists."""
    d = _entry_dir(entry, project_root)
    if (d / _MAIN_FILE).is_file():
        return d / _MAIN_FILE
    f = _entry_file(entry, project_root)
    if f.is_file():
        return f
    return None


def _parse_entry(text: str) -> tuple[dict[str, str], str]:
    """Split ``--- key: value --- body`` frontmatter; tolerant of hand edits."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            body = parts[2].lstrip("\n")
    return meta, body


def _format_entry(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key in ("name", "description", "author", "updated"):
        value = (meta.get(key) or "").replace("\n", " ").strip()
        if value:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def _default_description(content: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip().lstrip("#- ").strip()
        if stripped:
            return stripped[:_MAX_DESCRIPTION_CHARS]
    return ""


def _read_meta(path: Path, fallback_name: str) -> dict[str, Any] | None:
    try:
        meta, body = _parse_entry(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return {
        "name": meta.get("name") or fallback_name,
        "description": meta.get("description") or _default_description(body),
        "author": meta.get("author") or "",
        "updated": meta.get("updated") or "",
        "chars": len(body),
    }


def _list_subs(entry: str, project_root: str) -> list[dict[str, Any]]:
    d = _entry_dir(entry, project_root)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.md")):
        if path.name == _MAIN_FILE:
            continue
        info = _read_meta(path, f"{entry}/{path.stem}")
        if info is not None:
            info["name"] = f"{entry}/{path.stem}"
            out.append(info)
    return out


def list_entries(project_root: str = "") -> list[dict[str, Any]]:
    """Index of a project's entries (frontmatter only + sub index), newest-updated first."""
    d = memory_dir(project_root)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in d.glob("*.md"):
        info = _read_meta(path, path.stem)
        if info is None:
            continue
        info["subs"] = []
        out.append(info)
        seen.add(path.stem)
    for sub_dir in d.iterdir():
        if not sub_dir.is_dir() or sub_dir.name in seen:
            continue
        main = sub_dir / _MAIN_FILE
        if not main.is_file():
            continue
        info = _read_meta(main, sub_dir.name)
        if info is None:
            continue
        info["name"] = sub_dir.name
        info["subs"] = [
            {"name": s["name"], "description": s["description"]}
            for s in _list_subs(sub_dir.name, project_root)
        ]
        out.append(info)
    out.sort(key=lambda e: str(e.get("updated") or ""), reverse=True)
    return out


def read_entry(name: str, project_root: str = "") -> dict[str, Any] | None:
    """Read ``entry`` (main body + sub index) or ``entry/sub`` (that sub's body)."""
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    if sub:
        path = _entry_dir(entry, project_root) / f"{sub}.md"
        if not path.is_file():
            return None
        meta, body = _parse_entry(path.read_text(encoding="utf-8"))
        return {
            "name": f"{entry}/{sub}",
            "description": meta.get("description") or _default_description(body),
            "author": meta.get("author") or "",
            "updated": meta.get("updated") or "",
            "content": body,
            "path": str(path),
        }
    main = _main_path(entry, project_root)
    if main is None:
        return None
    meta, body = _parse_entry(main.read_text(encoding="utf-8"))
    return {
        "name": entry,
        "description": meta.get("description") or _default_description(body),
        "author": meta.get("author") or "",
        "updated": meta.get("updated") or "",
        "content": body,
        "subs": [
            {"name": s["name"], "description": s["description"]}
            for s in _list_subs(entry, project_root)
        ],
        "path": str(main),
    }


def _split_entry_to_dir(entry: str, project_root: str) -> None:
    """Convert a flat ``entry.md`` into ``entry/MEMORY.md`` so subs can nest under it."""
    flat = _entry_file(entry, project_root)
    d = _entry_dir(entry, project_root)
    d.mkdir(parents=True, exist_ok=True)
    if flat.is_file() and not (d / _MAIN_FILE).is_file():
        flat.rename(d / _MAIN_FILE)


def save_entry(
    name: str,
    content: str,
    *,
    description: str = "",
    author: str = "",
    project_root: str = "",
) -> dict[str, Any]:
    """Create or replace ``entry`` or ``entry/sub`` in THIS project's memory."""
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    body = (content or "").strip()
    if not body:
        raise ValueError("content is required")
    if len(body) > _MAX_ENTRY_CHARS:
        body = body[-_MAX_ENTRY_CHARS:]

    memory_dir(project_root, for_write=True)
    existing = read_entry(slug, project_root)
    meta = {
        "name": slug,
        "description": (description or "").strip()[:_MAX_DESCRIPTION_CHARS]
        or (existing or {}).get("description")
        or _default_description(body),
        "author": (author or "").strip() or (existing or {}).get("author") or "",
        "updated": _now_stamp(),
    }

    if sub:
        # Saving a sub splits the parent into dir form (creating a stub main if new).
        _split_entry_to_dir(entry, project_root)
        d = _entry_dir(entry, project_root)
        if not (d / _MAIN_FILE).is_file():
            (d / _MAIN_FILE).write_text(
                _format_entry(
                    {
                        "name": entry,
                        "description": f"Split memory topic — see its sub-entries (e.g. {slug})",
                        "author": meta["author"],
                        "updated": meta["updated"],
                    },
                    f"Index topic for `{entry}` — content lives in its sub-entries.",
                ),
                encoding="utf-8",
            )
        path = d / f"{sub}.md"
    else:
        d = _entry_dir(entry, project_root)
        path = (d / _MAIN_FILE) if (d / _MAIN_FILE).is_file() else _entry_file(entry, project_root)

    path.write_text(_format_entry(meta, body), encoding="utf-8")
    return {**meta, "chars": len(body), "path": str(path)}


def append_entry(
    name: str,
    text: str,
    *,
    author: str = "",
    description: str = "",
    project_root: str = "",
) -> dict[str, Any]:
    """Append an attributed block to ``entry`` or ``entry/sub``, creating it if missing."""
    slug = slugify_entry_name(name)
    note = (text or "").strip()
    if not note:
        raise ValueError("text is required")
    existing = read_entry(slug, project_root)
    if existing is None:
        return save_entry(
            slug, note, description=description, author=author, project_root=project_root
        )
    who = (author or "").strip()
    suffix = f" — {who}" if who else ""
    body = existing["content"].strip() + f"\n\n## {_now_stamp()}{suffix}\n{note}"
    return save_entry(
        slug,
        body,
        description=description or existing["description"],
        author=who or existing["author"],
        project_root=project_root,
    )


def delete_entry(name: str, project_root: str = "") -> bool:
    """Delete ``entry`` (with all its subs) or just one ``entry/sub``."""
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    if sub:
        path = _entry_dir(entry, project_root) / f"{sub}.md"
        if not path.is_file():
            return False
        path.unlink()
        return True
    removed = False
    flat = _entry_file(entry, project_root)
    if flat.is_file():
        flat.unlink()
        removed = True
    d = _entry_dir(entry, project_root)
    if d.is_dir() and (d / _MAIN_FILE).is_file():
        for child in d.glob("*.md"):
            child.unlink()
        try:
            d.rmdir()
        except OSError:
            pass
        removed = True
    return removed


def index_markdown(
    project_root: str = "",
    *,
    max_entries: int = 60,
    max_chars: int = 3_000,
    author_filter: str = "",
) -> str:
    """One line per entry (subs indented beneath) for prompt injection — never bodies.

    When ``author_filter`` is set, only shared entries (empty author) and entries
    authored by that ducky are listed — other duckies' notes stay out of the prompt.
    """
    entries = list_entries(project_root)
    want = (author_filter or "").strip().lower()
    if want:
        entries = [
            e
            for e in entries
            if not str(e.get("author") or "").strip()
            or str(e.get("author") or "").strip().lower() == want
        ]
    lines: list[str] = []
    used = 0
    shown = 0
    for e in entries[:max_entries]:
        who = str(e.get("author") or "")
        attribution = f" ({who})" if who else ""
        line = f"- {e['name']} — {e['description']}{attribution}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
        for s in e.get("subs") or []:
            sub_line = f"  - {s['name']} — {s['description']}"
            if used + len(sub_line) > max_chars:
                break
            lines.append(sub_line)
            used += len(sub_line) + 1
    if shown < len(entries):
        lines.append(f"- …+{len(entries) - shown} more — call project_memory_list for the full index")
    return "\n".join(lines)
