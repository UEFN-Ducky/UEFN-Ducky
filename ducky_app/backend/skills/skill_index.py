"""The agent's skill index (index-only prompt) and lazy subskill reads.

Full SKILL.md / reference bodies stay lazy via :func:`skill_read_subskill`.
"""

from __future__ import annotations

from typing import Any

from backend.skills.discovery import normalize_pack_id, normalize_subskill_id
from backend.skills.frontmatter import _index_tag_label, _pack_origin_tag, _subskill_origin_tag
from backend.skills.layout import CORE_ID
from backend.skills.manifest import (
    list_root_subskills,
    list_subskills,
    load_pack_manifest,
    read_subskill_body,
)
from backend.skills.revision import _NEW_CHAT_SKILL_TEXT, skills_revision
from backend.skills.seeding import seed_skill_packs
from backend.skills.selection import (
    SkillSelection,
    active_disabled_packs,
    active_enabled_subskills,
    default_skill_selection,
    merge_selection,
)

# --- Prompt assembly (index-only — full bodies via skill_read_subskill) ---


_ALWAYS_ON_PROMPT_CAP = 4000  # chars of injected always_on bodies (excludes index)

# Same-source Blender specialist packs — one index line unless the ducky
# opted in via enabled_subskills[pack_id].
_SPECIALIST_SUFFIXES = ("-style", "-mood", "-worlds", "-artist")
_SPECIALIST_PREFIXES = ("genre-",)


def _is_specialist_pack(pack_id: str) -> bool:
    p = (pack_id or "").strip().lower()
    return p.endswith(_SPECIALIST_SUFFIXES) or p.startswith(_SPECIALIST_PREFIXES)


def _specialist_opted_in(sel: SkillSelection, pack_id: str) -> bool:
    return pack_id in (sel.enabled_subskills or {})


def _label_with_blurb(label: str, manifest: dict[str, Any], limit: int) -> str:
    """``Label: the start of the pack description`` on one line (blurb cut to ``limit`` chars)."""
    desc = " ".join(str(manifest.get("description") or "").split())
    if not desc or desc in (label, ">-", ">", "|"):
        return label
    if desc.lower().startswith(label.lower()):
        desc = desc[len(label):].lstrip(" :—–-")
    if len(desc) > limit:
        desc = desc[:limit].rsplit(" ", 1)[0].rstrip(",;:—–- ") + "…"
    return f"{label}: {desc}" if desc else label


def build_skill_prompt(selection: SkillSelection | None = None) -> str:
    """Compact pack + subskill index, plus bodies of non-core always_on refs.

    Full SKILL.md / other refs stay lazy via ``skill_read_subskill``.
    Tags: ``[yours]`` (user-created), ``[store]``, ``[shipped]``, ``[plugin]``.
    """
    sel = selection or default_skill_selection()
    lines: list[str] = [
        "## Available skill packs (lazy-loaded)",
        "Tags: [yours]=you created, [store]=Store, [shipped]=bundled with app, [plugin]=desktop plugin.",
        'Not sure which pack or file covers it? skill_search("<what you need, in plain words>") '
        "returns the matching section and the exact skill_read_subskill call to read it.",
        'Call skill_read_subskill("<pack_id>", "core") to load a pack\'s SKILL.md.',
        'Call skill_read_subskill("<pack_id>") with no id to list its reference files, then load one by id.',
        "Load only what the current task needs — do not fetch every pack.",
        "Always-on rules below are already injected — follow them without re-fetching.",
        "",
    ]
    any_pack = False
    always_bodies: list[str] = []
    always_budget = _ALWAYS_ON_PROMPT_CAP
    collapsed: list[str] = []
    for pack_id in sel.enabled_packs:
        if _is_specialist_pack(pack_id) and not _specialist_opted_in(sel, pack_id):
            collapsed.append(pack_id)
            any_pack = True
            continue
        manifest = load_pack_manifest(pack_id)
        if not manifest:
            continue
        any_pack = True
        pack_tag = _pack_origin_tag(manifest)
        tag = _index_tag_label(pack_tag)
        label = str(manifest.get("label") or pack_id)
        lines.append(f"- `{pack_id}` [{tag}] — {_label_with_blurb(label, manifest, 100)}")
        for sub in list_subskills(manifest):
            sid = str(sub.get("id") or "")
            if not sid or not _subskill_allowed(pack_id, sub):
                continue
            # Inject non-core always_on bodies (capped) so agents get hard rules.
            if sid != CORE_ID and bool(sub.get("always_on")) and always_budget > 0:
                body = (read_subskill_body(pack_id, sid) or "").strip()
                if body:
                    chunk = f"### {pack_id}/{sid}\n\n{body}"
                    if len(chunk) > always_budget:
                        chunk = chunk[: always_budget - 1].rstrip() + "…"
                    always_bodies.append(chunk)
                    always_budget -= len(chunk)
            sub_tag = _index_tag_label(_subskill_origin_tag(sub, pack_tag))
            lines.append(f"  - `{sid}` [{sub_tag}]")
    if collapsed:
        listed = ", ".join(f"`{pid}`" for pid in collapsed[:40])
        extra = f" +{len(collapsed) - 40} more" if len(collapsed) > 40 else ""
        lines.append(
            f"- blender-styles [shipped] — {len(collapsed)} style/mood/world/artist/genre "
            f"packs: {listed}{extra}. Load one with skill_read_subskill when that look is needed."
        )
    if not any_pack:
        return "(no skill packs available)"
    if always_bodies:
        lines.append("")
        lines.append("## Always-on skill rules (injected)")
        lines.append("")
        lines.extend(always_bodies)
    return "\n".join(lines)


def build_skill_prompt_compact(selection: SkillSelection | None = None) -> str:
    """Local/Ollama: pack + subskill titles only (no blurbs, no always-on bodies).

    Full SKILL.md / refs stay lazy via ``skill_read_subskill`` — same architecture,
    ~7k → ~2k tokens for a full pack install.
    """
    sel = selection or default_skill_selection()
    lines: list[str] = [
        "## Available skill packs (lazy-loaded)",
        'Find the right file with skill_search("<what you need>"). '
        'Call skill_read_subskill("<pack_id>", "core") for SKILL.md; omit id to list refs.',
        "Load only what the current task needs. Titles are not the skill — do not invent APIs or files from a title.",
        "Never claim a file exists unless workspace_write_file returned that path this turn.",
        "",
    ]
    any_pack = False
    collapsed: list[str] = []
    for pack_id in sel.enabled_packs:
        if _is_specialist_pack(pack_id) and not _specialist_opted_in(sel, pack_id):
            collapsed.append(pack_id)
            any_pack = True
            continue
        manifest = load_pack_manifest(pack_id)
        if not manifest:
            continue
        any_pack = True
        pack_tag = _pack_origin_tag(manifest)
        tag = _index_tag_label(pack_tag)
        label = str(manifest.get("label") or pack_id)
        lines.append(f"- `{pack_id}` [{tag}] — {_label_with_blurb(label, manifest, 60)}")
        for sub in list_subskills(manifest):
            sid = str(sub.get("id") or "")
            if not sid or not _subskill_allowed(pack_id, sub):
                continue
            lines.append(f"  - `{sid}`")
    if collapsed:
        listed = ", ".join(f"`{pid}`" for pid in collapsed[:40])
        extra = f" +{len(collapsed) - 40} more" if len(collapsed) > 40 else ""
        lines.append(
            f"- blender-styles — {len(collapsed)} packs: {listed}{extra}. "
            "Load one with skill_read_subskill when needed."
        )
    if not any_pack:
        return "(no skill packs available)"
    return "\n".join(lines)


def _subskill_allowed(pid: str, sub: dict[str, Any]) -> bool:
    """True when the active allowlist permits this subskill (always_on / core always ok)."""
    sid = str(sub.get("id") or "").strip()
    if not sid:
        return False
    if bool(sub.get("always_on")) or sid == CORE_ID:
        return True
    allow = active_enabled_subskills()
    if allow is None or pid not in allow:
        return True
    return sid in allow[pid]


def _find_section(body: str, section: str) -> tuple[str, str] | None:
    """(heading, text) of the ``##``/``###`` section named ``section`` (case and punctuation ignored)."""
    from backend.search.chunker import slug, split_sections

    want = slug(section)
    _, sections = split_sections(body)
    for heading, text in sections:
        if slug(heading) == want:
            return heading, text
    for heading, text in sections:
        if want and (want in slug(heading) or slug(heading) in want):
            return heading, text
    return None


def skill_read_subskill(pack_id: str, subskill_id: str = "", section: str = "") -> str:
    pid = normalize_pack_id(pack_id)
    if pid in active_disabled_packs():
        return f"ERROR: pack denied for this ducky: {pid}"
    manifest = load_pack_manifest(pid)
    if not manifest:
        return f"ERROR: pack not found: {pid}"
    pack_label = str(manifest.get("label") or pid)
    if not subskill_id.strip():
        pack_tag = _pack_origin_tag(manifest)
        lines = [f"# {pack_label} reference files", ""]
        for sub in list_root_subskills(manifest):
            if not _subskill_allowed(pid, sub):
                continue
            sid = str(sub.get("id", ""))
            tag = _index_tag_label(_subskill_origin_tag(sub, pack_tag))
            desc = str(sub.get("description") or sub.get("label") or sid)
            lines.append(f"- `{sid}` [{tag}]: {desc}")
        return "\n".join(lines)
    sid = normalize_subskill_id(subskill_id)
    sub = next((s for s in list_subskills(manifest) if str(s.get("id")) == sid), {})
    if sub and not _subskill_allowed(pid, sub):
        return f"ERROR: subskill denied for this ducky: {pid}/{sid}"
    body = read_subskill_body(pid, sid)
    if not body:
        return f"ERROR: subskill not found: {pid}/{sid}"
    sub_label = str(sub.get("label") or sid)
    heading = f"## {pack_label}" if sid == CORE_ID else f"## {pack_label} — {sub_label}"
    if section.strip():
        found = _find_section(body, section)
        if found is None:
            from backend.search.chunker import split_sections

            names = [h for h, _ in split_sections(body)[1]]
            listed = "\n".join(f"- {h}" for h in names) or "(this file has no ## sections; read it whole)"
            return f"ERROR: no section {section!r} in {pid}/{sid}. Sections:\n{listed}"
        title, text = found
        return f"{heading} > {title}\n\n{text.strip()}"
    return f"{heading}\n\n{_fit_whole_sections(body.strip(), pid, sid)}"


# What one agent tool result holds after escaping and the envelope
# (agent.tool_result_compact.API_TOOL_RESULT_MAX is 16,000).
_WHOLE_READ_MAX = 12_000


def _fit_whole_sections(body: str, pid: str, sid: str) -> str:
    """A file too big for one read: its leading whole sections, then the rest by name with
    the call that reads each one. The verse core is 36k: cut by the result cap, its tail
    was lost without a word."""
    from backend.search.chunker import _FENCE, _HEADING, split_sections

    lines = body.splitlines()
    size, last_heading, current, in_code = 0, 0, "", False
    for i, line in enumerate(lines):
        m = None
        if _FENCE.match(line):
            in_code = not in_code
        elif not in_code and (m := _HEADING.match(line)):
            last_heading, current = i, m.group(2).strip()
        size += len(line) + 1
        if size > _WHOLE_READ_MAX:
            cut = last_heading or i
            break
    else:
        return body
    rest = split_sections("\n".join(lines[cut:]))
    names = [f"- {h} ({len(t):,} chars)" for h, t in rest[1]]
    if rest[0] and current:  # one section alone is too long: it stops mid-way above
        names.insert(0, f"- {current} (continues past this read)")
    call = f'skill_read_subskill(pack_id="{pid}", subskill_id="{sid}", section="<name>")'
    return (
        "\n".join(lines[:cut]).rstrip()
        + f"\n\n…This file is {len(body):,} chars, more than one read holds. Read each part "
        f"below once, when you need it, with {call}:\n" + "\n".join(names)
    )


# --- Combined text (agent prompt entrypoints) ---


def load_skill_text() -> str:
    seed_skill_packs()
    return build_skill_prompt(default_skill_selection())


def conversation_skill_text(
    *,
    enabled_skills: list[str] | None = None,
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    disabled_packs: list[str] | None = None,
    skill_snapshot: str = "",
    mutate: bool = True,
) -> str:
    del enabled_skills  # legacy filename allowlist ignored
    if skill_snapshot.strip():
        return skill_snapshot
    if mutate:
        seed_skill_packs()
    sel = merge_selection(
        enabled_packs=enabled_packs,
        enabled_subskills=enabled_subskills,
        disabled_packs=disabled_packs,
    )
    rev = skills_revision()
    key = (rev, repr(sel))
    text = _NEW_CHAT_SKILL_TEXT.get(key) if rev else None
    if text is None:
        text = build_skill_prompt(sel)
        if rev:
            if len(_NEW_CHAT_SKILL_TEXT) > 32:  # a handful of selections; never grows
                _NEW_CHAT_SKILL_TEXT.clear()
            _NEW_CHAT_SKILL_TEXT[key] = text
    return text
