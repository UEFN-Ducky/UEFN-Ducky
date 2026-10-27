"""Turn skill packs and tools into search rows.

A skill pack becomes one ``pack`` row, one ``subskill`` row per file (SKILL.md is
the ``core`` sub-skill) and one ``section`` row per ``##``/``###`` heading, so a
search can name the exact section to read instead of a 30 KB file. A tool becomes
one ``tool`` row. Every row carries its heading path ("pack > sub-skill >
section"); the vector is computed from path + text, so a short section still
knows what it belongs to.

Pure functions over strings and folders: no database, no model.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from backend.store.repos.search import Doc

# Bump when the rows built from the same files change shape; every source re-indexes.
CHUNKER_VERSION = "4"
SECTION_MAX = 1200
CODE_BLOCK_MAX = 2400
SUBSKILL_INTRO = 600
TOOL_DESC_MAX = 2000
SNIPPET_MAX = 240
SEP = " > "

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SLUG_DROP = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG_DROP.sub("-", text.lower()).strip("-")[:60] or "section"


def words(name: str) -> str:
    """``wire_verse_device_ref`` -> ``wire verse device ref``."""
    return re.sub(r"[_\-.]+", " ", name).strip()


def content_hash(*parts: Any) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _finish(doc: Doc) -> Doc:
    # The version is part of the hash, so a new way of building rows or vectors re-embeds them.
    doc.content_hash = content_hash(
        CHUNKER_VERSION, doc.kind, doc.title, doc.path, doc.body, doc.summary, doc.meta, doc.origin, doc.plugin_id
    )
    return doc


def embed_text(doc: Doc) -> str:
    """What the meaning model reads for one row.

    A tool is its name and the first line of its description: a long docstring (usage
    notes, parameter rules) averaged into one vector drowns what the tool does.
    """
    if doc.kind == "tool":
        lead = str((doc.meta or {}).get("description") or "")
        return f"{doc.title}\n{lead}".strip()
    head = doc.path or doc.title
    if doc.title and doc.title not in head:
        head = f"{head}{SEP}{doc.title}"
    return f"{head}\n{doc.body}".strip()


# --------------------------------------------------------------------------- markdown


def split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """(intro before the first heading, [(heading, text), ...]); headings in code blocks are text."""
    intro: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_code = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            sections.append((m.group(2).strip().strip("`*_ ").strip(), []))
            continue
        (sections[-1][1] if sections else intro).append(line)
    return "\n".join(intro).strip(), [(h, "\n".join(lines).strip()) for h, lines in sections]


def _blocks(text: str) -> list[str]:
    """Paragraphs, with each fenced code block kept as one block."""
    out: list[str] = []
    buf: list[str] = []
    in_code = False
    for line in text.splitlines():
        if _FENCE.match(line):
            if not in_code and buf:
                out.append("\n".join(buf).strip())
                buf = []
            buf.append(line)
            in_code = not in_code
            if not in_code:
                out.append("\n".join(buf).strip())
                buf = []
            continue
        if not in_code and not line.strip():
            if buf:
                out.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        out.append("\n".join(buf).strip())
    return [b for b in out if b]


def split_long(text: str) -> list[str]:
    """Pieces of at most SECTION_MAX characters on paragraph boundaries (code up to CODE_BLOCK_MAX)."""
    if len(text) <= SECTION_MAX:
        return [text] if text.strip() else []
    pieces: list[str] = []
    cur = ""
    for block in _blocks(text):
        limit = CODE_BLOCK_MAX if block.lstrip().startswith(("```", "~~~")) else SECTION_MAX
        if len(block) > limit:
            block = block[:limit]
        if cur and len(cur) + 2 + len(block) > SECTION_MAX:
            pieces.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        pieces.append(cur)
    return pieces


def snippet(text: str, limit: int = SNIPPET_MAX) -> str:
    one = re.sub(r"\s+", " ", re.sub(r"```[a-zA-Z0-9]*", " ", text or "")).strip()
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- skills


def _keywords(meta: dict[str, Any]) -> str:
    md = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    raw = meta.get("keywords") or md.get("keywords") or ""
    return str(raw).strip("[] ")


def pack_docs(
    pack_id: str,
    root: Path,
    manifest: dict[str, Any],
    *,
    origin: str,
    plugin_id: str = "",
) -> list[Doc]:
    """Rows for one pack folder (SKILL.md + references/*.md) described by ``manifest``."""
    from backend.skills.store import PACK_FILE, parse_frontmatter

    source_key = f"pack:{pack_id}"
    label = str(manifest.get("label") or pack_id)
    common = {"source_key": source_key, "origin": origin, "plugin_id": plugin_id, "pack_id": pack_id}
    subs = [s for s in manifest.get("subskills") or [] if isinstance(s, dict) and s.get("id")]
    pack_text = [str(manifest.get("description") or "")]
    sub_labels: list[str] = []
    docs: list[Doc] = []
    for sub in subs:
        sid = str(sub["id"])
        rel = str(sub.get("file") or (PACK_FILE if sid == "core" else f"references/{sid}.md"))
        try:
            raw = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(raw)
        sub_label = str(sub.get("label") or sid)
        always_on = bool(sub.get("always_on")) or sid == "core"
        if sid != "core":
            sub_labels.append(sub_label if sub_label == sid else f"{sub_label} ({sid})")
        desc = str(sub.get("description") or meta.get("description") or "").strip()
        keywords = _keywords(meta)
        if sid == "core" and keywords:
            pack_text.append(keywords)
        intro, sections = split_sections(body)
        sub_path = f"{label}{SEP}{sub_label}" if sid != "core" else label
        # The file row says what the file covers (its intro, or the list of its sections);
        # the section rows hold the details, so a specific question lands on a section.
        if intro:
            lead = intro
        elif sections:
            lead = "Sections: " + "; ".join(h for h, _ in sections)
        else:
            lead = body
        sub_body = "\n".join(
            x for x in (desc, str(sub.get("load_condition") or ""), keywords, lead[:SUBSKILL_INTRO].strip()) if x
        )
        docs.append(_finish(Doc(
            doc_key=f"skill:{pack_id}/{sid}", kind="subskill", title=sub_label, path=sub_path,
            body=sub_body, subskill_id=sid, meta={"file": rel, "description": desc, "always_on": always_on}, **common,
        )))
        if not sections:
            sections = [(sub_label, body)] if len(body.strip()) > len(lead[:SUBSKILL_INTRO].strip()) else []
        seen: dict[str, int] = {}
        for heading, text in sections:
            base = slug(heading)
            seen[base] = seen.get(base, 0) + 1
            key = base if seen[base] == 1 else f"{base}-{seen[base]}"
            pieces = split_long(text) or ([heading] if heading else [])
            for i, piece in enumerate(pieces):
                part_key = key if i == 0 else f"{key}~{i + 1}"
                title = heading if i == 0 else f"{heading} ({i + 1})"
                docs.append(_finish(Doc(
                    doc_key=f"skill:{pack_id}/{sid}#{part_key}", kind="section", title=title,
                    path=f"{sub_path}{SEP}{heading}", body=piece, subskill_id=sid, section=heading,
                    meta={"file": rel, "always_on": always_on}, **common,
                )))
    if sub_labels:
        pack_text.append("Sub-skills: " + ", ".join(sub_labels))
    docs.insert(0, _finish(Doc(
        doc_key=f"skill:{pack_id}", kind="pack", title=label, path=label, body="\n".join(t for t in pack_text if t),
        meta={"description": str(manifest.get("description") or "")}, **common,
    )))
    return docs


# --------------------------------------------------------------------------- tools


def _schema_params(schema: Any) -> list[str]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    out: list[str] = []
    for name, spec in props.items():
        desc = str(spec.get("description") or "").strip() if isinstance(spec, dict) else ""
        out.append(f"{name} ({desc[:80]})" if desc else str(name))
    return out


def tool_docs(rows: Iterable[dict[str, Any]], *, source_key: str = "tools") -> list[Doc]:
    """One row per tool. Each input row: name, description, inputSchema, aliases,
    owner_label (who provides it) and plugin_id (desktop plugin owner, if any)."""
    docs: list[Doc] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        desc = str(row.get("description") or "").strip()[:TOOL_DESC_MAX]
        lead, _, rest = desc.partition("\n")
        aliases = [a for a in row.get("aliases") or [] if isinstance(a, str)]
        owner = str(row.get("owner_label") or "")
        about = str(row.get("owner_about") or "")
        params = _schema_params(row.get("inputSchema"))
        body = "\n".join(x for x in (
            words(name.split("__", 1)[-1]),
            rest.strip(),
            f"{owner.removeprefix('Plugin: ')}: {about}" if about else "",
            ("Also called: " + ", ".join(aliases)) if aliases else "",
            ("Parameters: " + ", ".join(params)) if params else "",
        ) if x)
        docs.append(_finish(Doc(
            doc_key=f"tool:{name}", source_key=source_key, kind="tool", title=words(name.split("__", 1)[-1]),
            # The first description line moves to its own keyword column, weighted above the
            # body: inside a long docstring it counted no more than a usage note did. Moved,
            # not copied, so row lengths (and every other row's bm25) stay the same.
            path=owner, body=body, summary=lead,
            origin="plugin" if row.get("plugin_id") else ("mcp" if "__" in name else "core"),
            plugin_id=str(row.get("plugin_id") or ""), tool_name=name,
            meta={"aliases": aliases, "owner": owner, "description": lead[:300]},
        )))
    return docs


def record_doc(plugin_id: str, scope: str, record_id: str, title: str, text: str, meta: dict[str, Any] | None) -> Doc:
    return _finish(Doc(
        doc_key=f"record:{plugin_id}:{scope}:{record_id}", source_key=f"record:{plugin_id}:{scope}", kind="record",
        title=title, path=title, body=text, origin="plugin", plugin_id=plugin_id, scope=scope,
        # The plugin's meta as given: the record id comes back from doc_key, so a meta
        # "id" of its own can neither replace it nor be lost.
        meta=dict(meta or {}),
    ))
