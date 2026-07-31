"""Push skill packs to AppData and to every configured IDE whenever MCP is applied.

Packs live in the open Agent Skills layout (SKILL.md + references/*.md), so
deployment is a straight folder copy that Claude Code, Cursor, and any other
SKILL.md-aware agent can consume natively:

**Apply** (one IDE or all): merge MCP config + refresh AppData packs + copy every
pack into ``<config_dir>/skills/<pack_id>/`` beside that IDE's config (and into
``~/.claude/skills/`` for Claude Code) + write the legacy combined
``UEFN-Ducky-SKILL.md`` beside the config.

**MCP update** (panel launch, bridge start, Deploy): same refresh for every
configured IDE path.

Deployed folders are tagged ``metadata.managed_by: uefn-ducky`` in their
SKILL.md frontmatter; only tagged folders are pruned when a pack disappears,
so user-authored skills in the same directory are never touched.

Agents may also consume the combined guide via MCP tool ``uefn_skill``.
Nothing is written into the UEFN project ``Content/`` folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence

import backend.skills.store as _skill

PROJECT_SKILL_FILENAME = "UEFN-Ducky-SKILL.md"
SKILLS_DIRNAME = "skills"
MANAGED_BY = "uefn-ducky"


def seed_skill_appdata(force: bool = False) -> tuple[Path | None, bool]:
    """Seed bundled skill packs into AppData. Returns ``(packs_dir, upgraded)``."""
    logs = _skill.seed_skill_packs(force=force)
    dest = _skill.appdata_skill_packs_dir()
    upgraded = any("seeded" in ln for ln in logs)
    if dest.is_dir():
        return dest, upgraded
    return None, False


def write_skill_beside_config(config_path: Path, text: str | None = None) -> Path | None:
    """Write a reference copy of the combined skill text next to an IDE config file.

    ``text`` lets one sync run reuse a single build — ``load_skill_text()`` walks
    every pack and is ~1s a call, and it used to run once per IDE config.
    """
    try:
        dest = Path(config_path).parent / PROJECT_SKILL_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = _skill.load_skill_text() if text is None else text
        _write_if_changed(dest, body.encode("utf-8"))
        return dest
    except OSError:
        return None


def _pack_source_dir(pack_id: str) -> Path | None:
    """First existing pack root (AppData, plugin-owned, or bundled)."""
    for root in _skill._pack_roots(pack_id):
        if (root / _skill.PACK_FILE).is_file():
            return root
    return None


def _deployed_skill_md(pack_id: str) -> str | None:
    """SKILL.md for deployment: tagged as managed, with a reference index appended."""
    src_dir = _pack_source_dir(pack_id)
    if src_dir is None:
        return None
    try:
        raw = (src_dir / _skill.PACK_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _skill.parse_frontmatter(raw)
    md = meta.setdefault("metadata", {})
    if isinstance(md, dict):
        md["managed_by"] = MANAGED_BY
        owner = _skill.plugin_owner_for_skill(pack_id)
        if owner:
            md["source_plugin_id"] = owner
    manifest = _skill.load_pack_manifest(pack_id) or {}
    ref_lines: list[str] = []
    for sub in _skill.list_subskills(manifest):
        sid = str(sub.get("id") or "")
        if not sid or sid == _skill.CORE_ID:
            continue
        tag = _skill.subskill_index_tag(sub, manifest)
        desc = str(sub.get("description") or sub.get("label") or sid)
        ref_lines.append(f"- `{sid}` [{tag}] — {desc}")
        cond = str(sub.get("load_condition") or "").strip()
        if cond:
            ref_lines.append(f"  Load when: {cond}")
    if ref_lines:
        # Prefer MCP skill_read_subskill: IDE Read on ~/.claude|~/.cursor/skills
        # hits workspace permission prompts and fails. Keep ids (not file paths)
        # so agents do not try filesystem Reads of references/*.md.
        body = (
            body.strip()
            + "\n\n## Reference files\n\n"
            + "Tags: [yours]=you created, [store]=Store, [shipped]=bundled, [plugin]=plugin.\n\n"
            + f'Load with MCP `skill_read_subskill("{pack_id}", "<id>")` when needed. '
            + "Do **not** use the IDE Read/open-file tool on "
            + "`~/.claude/skills`, `~/.cursor/skills`, or `references/*.md` paths "
            + "(outside the project workspace — permission prompts / always errors).\n\n"
            + "\n".join(ref_lines)
        )
    return _skill.serialize_frontmatter(meta, body)


def _is_managed_skill_dir(path: Path) -> bool:
    raw = None
    try:
        raw = (path / _skill.PACK_FILE).read_text(encoding="utf-8")
    except OSError:
        return False
    meta, _ = _skill.parse_frontmatter(raw)
    md = meta.get("metadata")
    return isinstance(md, dict) and md.get("managed_by") == MANAGED_BY


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write only when the bytes differ. Returns True when the file was touched."""
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.write_bytes(data)
    return True


def _sync_references(src: Path | None, dest: Path) -> bool:
    """Mirror ``src`` into ``dest``, copying only changed files. True if anything moved.

    Replaces a blind rmtree + copytree: that rewrote ~200 files on every Apply
    (and on every plugin register auto-apply), which is what made a cold boot pay
    tens of seconds of AV-scanned disk churn for byte-identical content.
    """
    if src is None or not src.is_dir():
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
            return True
        return False
    dest.mkdir(parents=True, exist_ok=True)
    touched = False
    wanted: set[str] = set()
    for child in src.rglob("*"):
        rel = child.relative_to(src)
        target = dest / rel
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            wanted.add(str(rel))
            continue
        wanted.add(str(rel))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if _write_if_changed(target, child.read_bytes()):
                touched = True
        except OSError:
            touched = True
    for child in sorted(dest.rglob("*"), reverse=True):
        rel = str(child.relative_to(dest))
        if rel in wanted:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
            touched = True
        except OSError:
            pass
    return touched


def deploy_skill_folders(skills_root: Path) -> list[str]:
    """Copy every pack into ``skills_root/<pack_id>/``; prune stale managed folders.

    Content-compares before writing — an unchanged tree costs reads only.
    """
    logs: list[str] = []
    pack_ids = _skill.list_pack_ids()
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return [f"Skill deploy skipped ({skills_root}): {e}"]
    for pack_id in pack_ids:
        skill_md = _deployed_skill_md(pack_id)
        if skill_md is None:
            continue
        dest = skills_root / pack_id
        src_dir = _pack_source_dir(pack_id)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            touched = _write_if_changed(
                dest / _skill.PACK_FILE, skill_md.encode("utf-8")
            )
            refs_src = (src_dir / _skill.REFERENCES_DIR) if src_dir else None
            touched |= _sync_references(refs_src, dest / _skill.REFERENCES_DIR)
            if touched:
                logs.append(f"Skill folder -> {dest}")
        except OSError as e:
            logs.append(f"Skill folder failed ({dest}): {e}")
    try:
        for child in skills_root.iterdir():
            if child.is_dir() and child.name not in pack_ids and _is_managed_skill_dir(child):
                shutil.rmtree(child)
                logs.append(f"Skill folder pruned -> {child}")
    except OSError:
        pass
    return logs


def registered_coding_agent_skills_roots() -> set[Path]:
    """Skills dirs declared by enabled gateway plugins (register_coding_agent)."""
    roots: set[Path] = set()
    try:
        from backend.uefn_plugins.host import (
            get_coding_agent_registration,
            registered_coding_agent_ids,
        )

        for aid in registered_coding_agent_ids():
            reg = get_coding_agent_registration(aid) or {}
            skills = reg.get("skills_dir")
            if callable(skills):
                try:
                    skills = skills()
                except Exception:
                    skills = None
            text = str(skills or "").strip()
            if text:
                roots.add(Path(text))
    except Exception:
        pass
    return roots


def ide_config_paths(antigravity_override: str = "") -> list[Path]:
    """Default MCP config paths for registered IDE hookups."""
    from frontend.ide_paths import IdeKind, path_for_ide
    from backend.uefn_plugins.host import registered_ide_hookup_kinds

    kinds = []
    for key in registered_ide_hookup_kinds():
        try:
            kinds.append(IdeKind(key))
        except ValueError:
            continue
    if not kinds:
        kinds = list(IdeKind)
    return [path_for_ide(k, antigravity_override) for k in kinds]


def sync_skill(
    *,
    ide_config_paths: Sequence[Path] | None = None,
    force_appdata: bool = False,
) -> list[str]:
    """Refresh AppData packs, IDE skill folders, and reference copies. Returns log lines.

    Also the source of truth for the **UEFN-Ducky in-panel agent** (it reads AppData
    packs via ``build_skill_prompt``). Custom / user skill folders without
    ``managed_by: uefn-ducky`` are never pruned.
    """
    logs: list[str] = []
    pack_logs = _skill.seed_skill_packs(force=force_appdata)
    logs.extend(pack_logs)
    dest = _skill.appdata_skill_packs_dir()
    if not dest.is_dir():
        logs.append("AppData skill_packs/ not written (bundled packs missing)")
        return logs

    # Gateway plugin skills dirs + every IDE config's sibling skills/ folder.
    skills_roots = set(registered_coding_agent_skills_roots())
    configs = list(ide_config_paths or ())
    combined = _skill.load_skill_text() if configs else ""
    for cfg in configs:
        beside = write_skill_beside_config(Path(cfg), combined)
        if beside:
            logs.append(f"Skill copy -> {beside}")
        skills_roots.add(Path(cfg).parent / SKILLS_DIRNAME)
    for root in sorted(skills_roots):
        logs.extend(deploy_skill_folders(root))

    return logs


def sync_skill_for_ide(config_path: Path | str, *, force_appdata: bool = False) -> list[str]:
    """After Apply to one IDE: upgrade AppData + deploy skills beside that config."""
    return sync_skill(ide_config_paths=[Path(config_path)], force_appdata=force_appdata)


def sync_skill_all_ides(antigravity_override: str = "", *, force_appdata: bool = False) -> list[str]:
    """On MCP/EXE startup: upgrade AppData + refresh skills beside every IDE config path."""
    return sync_skill(
        ide_config_paths=ide_config_paths(antigravity_override),
        force_appdata=force_appdata,
    )


def sync_skill_on_mcp_update(antigravity_override: str = "") -> list[str]:
    """Called when the MCP server or panel starts — same as ``sync_skill_all_ides``."""
    return sync_skill_all_ides(antigravity_override)
