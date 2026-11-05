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

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir
from backend.util.env_compat import env_str

_MAX_ENTRY_CHARS = 16_000
_MAX_DESCRIPTION_CHARS = 140
_MAX_SLUG_CHARS = 60

_MAIN_FILE = "MEMORY.md"
# The database has no private column: a private entry's author is stored with this prefix.
_PRIVATE_AUTHOR_PREFIX = "private:"


def is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in ("true", "yes", "1")


def _decode_author(raw: Any) -> tuple[str, bool]:
    """DB author column → (author, private)."""
    text = str(raw or "")
    if text.startswith(_PRIVATE_AUTHOR_PREFIX):
        return text[len(_PRIVATE_AUTHOR_PREFIX):], True
    return text, False


def resolve_project_root(explicit: str = "") -> str:
    """``explicit``, else the project this call works in (backend.bridge.client)."""
    if (explicit or "").strip():
        return explicit.strip()
    try:
        from backend.bridge.client import current_project_root

        return current_project_root()
    except Exception:
        return env_str("PROJECT_ROOT").strip()


def memory_dir(project_root: str = "", *, for_write: bool = False) -> Path:
    # Same per-project keying as chats ("_no_project" when no root is set); the
    # slug embeds a path hash, so same-named projects can never share a folder.
    from frontend.ui_web.chats.store import project_slug

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


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("memory")


def _project_id(project_root: str) -> str:
    from frontend.ui_web.chats.store import project_slug

    return project_slug(resolve_project_root(project_root))


def _repo():
    from backend.store.importers import phase4
    from backend.store.repos import memory as repo

    phase4.ensure("memory")
    return repo


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
    for key in ("name", "description", "author", "private", "updated"):
        value = str(meta.get(key) or "").replace("\n", " ").strip()
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
        "private": is_true(meta.get("private")),
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


def stored_author(author: str, private: bool) -> str:
    """The DB author column for an entry: private entries carry _PRIVATE_AUTHOR_PREFIX."""
    return f"{_PRIVATE_AUTHOR_PREFIX}{author}" if private else author


def _sub_index_item(name: str, description: str, author: str, private: bool) -> dict[str, Any]:
    """A sub line of the index: name + description; a private sub also says who may see it."""
    item: dict[str, Any] = {"name": name, "description": description}
    if private:
        item.update(private=True, author=author)
    return item


def list_entries(project_root: str = "") -> list[dict[str, Any]]:
    """Index of a project's entries (frontmatter only + sub index), newest-updated first."""
    if _use_db():
        rows = _repo().index(_project_id(project_root))
        for row in rows:
            row["author"], row["private"] = _decode_author(row.get("author"))
            subs = []
            for s in row.get("subs") or []:
                author, private = _decode_author(s.get("author"))
                subs.append(_sub_index_item(s["name"], s["description"], author, private))
            row["subs"] = subs
        return rows
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
            _sub_index_item(s["name"], s["description"], s["author"], s["private"])
            for s in _list_subs(sub_dir.name, project_root)
        ]
        out.append(info)
    out.sort(key=lambda e: str(e.get("updated") or ""), reverse=True)
    return out


def read_entry(name: str, project_root: str = "") -> dict[str, Any] | None:
    """Read ``entry`` (main body + sub index) or ``entry/sub`` (that sub's body)."""
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    if _use_db():
        repo = _repo()
        project_id = _project_id(project_root)
        row = repo.get(project_id, slug)
        if row is None:
            return None
        author, private = _decode_author(row["author"])
        out: dict[str, Any] = {
            "name": slug,
            "description": row["description"] or _default_description(row["body"]),
            "author": author,
            "private": private,
            "updated": row["updated"] or "",
            "content": row["body"],
            "path": "",
        }
        if not sub:
            out["subs"] = repo.subs_of(project_id, entry)
        return out
    if sub:
        path = _entry_dir(entry, project_root) / f"{sub}.md"
        if not path.is_file():
            return None
        meta, body = _parse_entry(path.read_text(encoding="utf-8"))
        return {
            "name": f"{entry}/{sub}",
            "description": meta.get("description") or _default_description(body),
            "author": meta.get("author") or "",
            "private": is_true(meta.get("private")),
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
        "private": is_true(meta.get("private")),
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
    private: bool | None = None,
) -> dict[str, Any]:
    """Create or replace ``entry`` or ``entry/sub`` in THIS project's memory.

    ``private=True`` keeps the entry out of every other ducky's index; ``None``
    keeps the existing flag.
    """
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    body = (content or "").strip()
    if not body:
        raise ValueError("content is required")
    if len(body) > _MAX_ENTRY_CHARS:
        body = body[-_MAX_ENTRY_CHARS:]

    if not _use_db():
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
    is_private = bool((existing or {}).get("private")) if private is None else bool(private)
    if is_private:
        meta["private"] = "true"

    if _use_db():
        repo = _repo()
        project_id = _project_id(project_root)
        if sub and repo.get(project_id, entry) is None:
            repo.put(
                project_id,
                entry,
                description=f"Split memory topic — see its sub-entries (e.g. {slug})",
                author=meta["author"],
                updated=meta["updated"],
                body=f"Index topic for `{entry}` — content lives in its sub-entries.",
            )
        repo.put(
            project_id,
            slug,
            description=meta["description"],
            author=stored_author(meta["author"], is_private),
            updated=meta["updated"],
            body=body,
        )
        return {**meta, "chars": len(body), "path": ""}

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
    """Append an attributed block to ``entry`` or ``entry/sub``, creating it if missing.

    A full entry rolls over to the next sub-entry (``entry/2``, ``entry/3`` …; a sub
    ``entry/sub`` rolls to ``entry/sub-2``) and says so in ``rolled_over``. save_entry
    keeps only the last _MAX_ENTRY_CHARS, so appending past the cap used to drop text
    while answering ok (Nov 4 2026, crash-watch). ``ok`` is False when nothing was stored.
    """
    slug = slugify_entry_name(name)
    note = (text or "").strip()
    if not note:
        raise ValueError("text is required")
    if len(note) > _MAX_ENTRY_CHARS:
        return {
            "ok": False,
            "name": slug,
            "appended_chars": 0,
            "error": f"note too long ({len(note)} chars, cap {_MAX_ENTRY_CHARS}): split it into sub-entries",
        }
    who = (author or "").strip()
    suffix = f" — {who}" if who else ""
    block = f"## {_now_stamp()}{suffix}\n{note}"
    entry, sub = _split_slug(slug)
    target = slug
    for n in range(2, 1000):
        existing = read_entry(target, project_root)
        if existing is None:
            body = note if target == slug else block
            saved = save_entry(target, body, description=description, author=author, project_root=project_root)
            break
        body = existing["content"].strip() + "\n\n" + block
        if len(body) <= _MAX_ENTRY_CHARS:
            saved = save_entry(
                target,
                body,
                description=description or existing["description"],
                author=who or existing["author"],
                project_root=project_root,
            )
            break
        target = f"{entry}/{n}" if not sub else f"{entry}/{sub}-{n}"
    else:
        return {"ok": False, "name": slug, "appended_chars": 0, "error": f"entry full ({_MAX_ENTRY_CHARS} chars): save a sub-entry"}
    out = {**saved, "ok": True, "appended_chars": len(note)}
    if target != slug:
        out["rolled_over"] = target
        out["note"] = f"{slug} is full ({_MAX_ENTRY_CHARS} chars); this note was stored in {target}."
    return out


def delete_entry(name: str, project_root: str = "") -> bool:
    """Delete ``entry`` (with all its subs) or just one ``entry/sub``."""
    slug = slugify_entry_name(name)
    entry, sub = _split_slug(slug)
    if _use_db():
        return _repo().delete(_project_id(project_root), slug, with_subs=not sub) > 0
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


CORE_PREFIXES = ("team-workflow", "decision", "architecture", "bug", "git", "testing")

_STOPWORDS = frozenset(
    "the and for with from that this into when then than what your you are was were has have not but all any "
    "its his her our out use one two can will via per".split()
)


def _words(text: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3 or w in _STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def _score(task_words: set[str], name: str, description: str) -> int:
    if not task_words:
        return 0
    return len(task_words & _words(f"{name.replace('/', ' ')} {description}"))


def _core_rank(name: str) -> int:
    """Position in CORE_PREFIXES for ``name`` (its top-level part), else -1."""
    top = name.split("/", 1)[0]
    for i, prefix in enumerate(CORE_PREFIXES):
        if top == prefix or top.startswith(prefix + "-"):
            return i
    return -1


def index_markdown(
    project_root: str = "",
    *,
    max_entries: int = 60,
    max_chars: int = 3_000,
    author_filter: str = "",
    task: str = "",
    private_author: str = "",
) -> str:
    """One line per entry (subs indented beneath) for prompt injection — never bodies.

    Every ducky's entries are listed: lessons are shared whoever wrote them. Only
    entries marked private (``private: true``) are hidden, except from their author
    (``private_author``; ``author_filter`` is its deprecated alias). Order: core
    prefixes (CORE_PREFIXES) first, then entries matching ``task``, then the rest
    newest first; subs stay under their parent, best ``task`` match first.
    """
    me = (private_author or author_filter or "").strip().lower()

    def visible(item: dict[str, Any]) -> bool:
        return not item.get("private") or bool(me and str(item.get("author") or "").strip().lower() == me)

    entries = [e for e in list_entries(project_root) if visible(e)]
    task_words = _words(task)
    ranked: list[tuple[tuple[int, int, int, int], dict[str, Any], list[dict[str, Any]]]] = []
    for pos, e in enumerate(entries):
        subs = [s for s in (e.get("subs") or []) if visible(s)]
        sub_scores = {s["name"]: _score(task_words, s["name"], s.get("description", "")) for s in subs}
        subs.sort(key=lambda s: -sub_scores[s["name"]])
        score = max([_score(task_words, e["name"], e.get("description", "")), *sub_scores.values()])
        core = _core_rank(e["name"])
        if core >= 0:
            key = (0, -score, core, pos)
        elif score > 0:
            key = (1, -score, 0, pos)
        else:
            key = (2, 0, 0, pos)
        ranked.append((key, e, subs))
    ranked.sort(key=lambda r: r[0])

    total = sum(1 + len(subs) for _, _, subs in ranked)
    # Room for the trailer line, so the whole index stays within max_chars.
    budget = max(0, max_chars - 70)
    lines: list[str] = []
    used = 0
    shown = 0
    full = False
    for _, e, subs in ranked[:max_entries]:
        for line in [f"- {e['name']} — {e.get('description', '')}"] + [
            f"  - {s['name']} — {s.get('description', '')}" for s in subs
        ]:
            if used + len(line) > budget:
                full = True
                break
            lines.append(line)
            used += len(line) + 1
            shown += 1
        if full:
            break
    if shown < total:
        lines.append(f"- …{total - shown} more entries left out — project_memory_list has them all")
    return "\n".join(lines)


def suggest_entries(text: str, project_root: str = "", *, limit: int = 3) -> list[str]:
    """Names of the entries (subs included) whose name + description best match ``text``."""
    words = _words(text)
    if not words or limit <= 0:
        return []
    scored: list[tuple[int, int, str]] = []
    pos = 0
    for e in list_entries(project_root):
        if e.get("private"):
            continue
        for item in [e, *(e.get("subs") or [])]:
            if item.get("private"):
                continue
            s = _score(words, item["name"], item.get("description", ""))
            if s > 0:
                scored.append((-s, pos, item["name"]))
            pos += 1
    scored.sort()
    return [name for _, _, name in scored[:limit]]
