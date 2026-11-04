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

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, Sequence

import backend.skills.discovery as _discovery
import backend.skills.manifest as _manifest
import backend.skills.seeding as _seeding
import backend.skills.skill_index as _skill_index
from backend.skills.frontmatter import parse_frontmatter, serialize_frontmatter, subskill_index_tag
from backend.skills.layout import CORE_ID, PACK_FILE, REFERENCES_DIR
from backend.skills.passes import pass_cached, pass_memo, shared_pass_memo

PROJECT_SKILL_FILENAME = "UEFN-Ducky-SKILL.md"
SKILLS_DIRNAME = "skills"
MANAGED_BY = "uefn-ducky"
# Ledger of the pack folders we deployed into a skills root. Lets us prune a
# folder we own even when its SKILL.md is gone/corrupt, so a pack that
# disappears can never leave a stale copy an agent could still read.
DEPLOY_LEDGER = ".ducky-skills.json"


def seed_skill_appdata(force: bool = False) -> tuple[Path | None, bool]:
    """Seed bundled skill packs into AppData. Returns ``(packs_dir, upgraded)``."""
    logs = _seeding.seed_skill_packs(force=force)
    dest = _discovery.appdata_skill_packs_dir()
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
        body = _skill_index.load_skill_text() if text is None else text
        if _write_if_changed(dest, body.encode("utf-8")):
            return dest
        return None
    except OSError:
        return None


def _pack_source_dir(pack_id: str) -> Path | None:
    """First existing pack root (AppData, plugin-owned, or bundled)."""
    for root in _discovery._pack_roots(pack_id):
        if (root / PACK_FILE).is_file():
            return root
    return None


def _deployed_skill_md(pack_id: str) -> str | None:
    """SKILL.md for deployment: tagged as managed, with a reference index appended."""
    src_dir = _pack_source_dir(pack_id)
    if src_dir is None:
        return None
    try:
        raw = (src_dir / PACK_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = parse_frontmatter(raw)
    # load_pack_manifest already looked up the owning plugin: one ownership lookup per pack.
    manifest = _manifest.load_pack_manifest(pack_id) or {}
    md = meta.setdefault("metadata", {})
    if isinstance(md, dict):
        md["managed_by"] = MANAGED_BY
        owner = str(manifest.get("source_plugin_id") or "").strip()
        if owner:
            md["source_plugin_id"] = owner
    ref_lines: list[str] = []
    for sub in _manifest.list_subskills(manifest):
        sid = str(sub.get("id") or "")
        if not sid or sid == CORE_ID:
            continue
        tag = subskill_index_tag(sub, manifest)
        desc = str(sub.get("description") or sub.get("label") or sid)
        ref_lines.append(f"- `{sid}` [{tag}] — {desc}")
        cond = str(sub.get("load_condition") or "").strip()
        if cond:
            ref_lines.append(f"  Load when: {cond}")
    if ref_lines:
        body = (
            body.strip()
            + f'\n\nRefs: skill_read_subskill("{pack_id}", "<id>") — never IDE-Read '
            + "`~/.claude/skills` / `~/.cursor/skills` / `references/*.md`.\n"
            + "\n".join(ref_lines)
        )
    return serialize_frontmatter(meta, body)


def _is_managed_skill_dir(path: Path) -> bool:
    raw = None
    try:
        raw = (path / PACK_FILE).read_text(encoding="utf-8")
    except OSError:
        return False
    meta, _ = parse_frontmatter(raw)
    md = meta.get("metadata")
    return isinstance(md, dict) and md.get("managed_by") == MANAGED_BY


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write only when the bytes differ. Returns True when the file was touched."""
    try:
        if path.read_bytes() == data:  # missing file raises: no separate stat
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
    # Every skills root mirrors the same source: walk and read it once per pass.
    src_dirs, src_files = pass_cached(("refs_tree", str(src)), lambda: _read_tree(src))
    have_dirs, have_files = _list_tree(dest)
    if not have_dirs and not have_files:
        dest.mkdir(parents=True, exist_ok=True)
    touched = False
    for rel in src_dirs:
        if rel not in have_dirs:
            (dest / rel).mkdir(parents=True, exist_ok=True)
    for rel, data in src_files.items():
        if data is None:
            touched = True  # unreadable source file
            continue
        target = dest / rel
        try:
            if rel in have_files and target.read_bytes() == data:
                continue
        except OSError:
            pass
        try:
            if os.sep in rel and os.path.dirname(rel) not in have_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            touched = True
        except OSError:
            touched = True
    stale = [rel for rel in have_files if rel not in src_files]
    stale += [rel for rel in have_dirs if rel not in src_dirs]
    for rel in sorted(stale, reverse=True):
        child = dest / rel
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
            touched = True
        except OSError:
            pass
    return touched


def _list_tree(root: Path) -> tuple[set[str], set[str]]:
    """(relative dirs, relative files) under ``root``; one os.walk, no per-file Path work."""
    dirs: set[str] = set()
    files: set[str] = set()
    base = str(root)
    for cur, subdirs, names in os.walk(base):
        rel = os.path.relpath(cur, base)
        prefix = "" if rel == "." else rel + os.sep
        dirs.update(prefix + d for d in subdirs)
        files.update(prefix + n for n in names)
    return dirs, files


def _read_tree(root: Path) -> tuple[set[str], dict[str, bytes | None]]:
    dirs, files = _list_tree(root)
    out: dict[str, bytes | None] = {}
    for rel in files:
        try:
            out[rel] = (root / rel).read_bytes()
        except OSError:
            out[rel] = None
    return dirs, out


def _read_ledger_doc(skills_root: Path) -> dict:
    try:
        data = json.loads((skills_root / DEPLOY_LEDGER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ledger_packs(data: dict) -> set[str]:
    packs = data.get("packs")
    return {str(p) for p in packs} if isinstance(packs, list) else set()


def _ledger_stamps(data: dict) -> dict[str, dict]:
    stamps = data.get("stamps")
    return {str(k): v for k, v in stamps.items() if isinstance(v, dict)} if isinstance(stamps, dict) else {}


def _read_deploy_ledger(skills_root: Path) -> set[str]:
    """Pack ids we deployed into this root on a previous run."""
    return _ledger_packs(_read_ledger_doc(skills_root))


def _write_deploy_ledger(
    skills_root: Path, pack_ids: Iterable[str], stamps: dict[str, dict] | None = None
) -> None:
    packs = sorted(set(pack_ids))
    doc: dict = {"packs": packs}
    if stamps:
        doc["stamps"] = {pid: stamps[pid] for pid in packs if pid in stamps}
    try:
        _write_if_changed(
            skills_root / DEPLOY_LEDGER, (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
    except OSError:
        pass


def _dest_state(dest: Path) -> dict[str, list[int]]:
    """``{rel: [mtime_ns, size]}`` for every file and folder under ``dest`` ({} when missing).

    Read from directory listings only: on Windows ``DirEntry.stat()`` comes with the
    listing, so an unchanged pack costs no file opens (each open is AV-scanned).
    """
    out: dict[str, list[int]] = {}
    stack = [(str(dest), "")]
    while stack:
        folder, prefix = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            rel = prefix + entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    out[rel + "/"] = [0, 0]
                    stack.append((entry.path, rel + "/"))
                else:
                    st = entry.stat(follow_symlinks=False)
                    out[rel] = [st.st_mtime_ns, st.st_size]
            except OSError:
                continue
    return out


def _source_key(skill_md: str, refs_src: Path | None) -> str:
    """Hash of everything a pack deploys: SKILL.md text + its references tree."""
    digest = hashlib.sha1(skill_md.encode("utf-8"))
    if refs_src is not None and refs_src.is_dir():
        dirs, files = pass_cached(("refs_tree", str(refs_src)), lambda: _read_tree(refs_src))
        for rel in sorted(dirs):
            digest.update(b"d\0" + rel.encode("utf-8") + b"\0")
        for rel in sorted(files):
            digest.update(b"f\0" + rel.encode("utf-8") + b"\0" + (files[rel] or b"\x00unreadable"))
    return digest.hexdigest()


def _prune_pack_root(dest: Path) -> list[str]:
    """Drop everything in a pack folder we own except ``SKILL.md`` + ``references/``.

    Without this, a file an older plugin version deployed at the pack root (an
    earlier layout that kept subskills there, a renamed SKILL, a stray LICENSE)
    survives every later update — ``_sync_references`` only mirrors
    ``references/``, and the pack-root write only touches ``SKILL.md``. Agents
    can still read those leftovers, so they must go.
    """
    keep = {PACK_FILE, REFERENCES_DIR}
    removed: list[str] = []
    try:
        children = list(dest.iterdir())
    except OSError:
        return removed
    for child in children:
        if child.name in keep:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
            removed.append(child.name)
        except OSError:
            pass
    return removed


def deploy_skill_folders(skills_root: Path) -> list[str]:
    """Copy every pack into ``skills_root/<pack_id>/``; prune stale managed folders.

    Content-compares before writing — an unchanged tree costs reads only.

    Leaves **no** stale file behind: the pack root is pruned to
    ``SKILL.md`` + ``references/`` (folders we own only), ``references/`` is
    mirrored exactly, and a pack that disappears is removed even if its
    SKILL.md no longer parses, via the deploy ledger.

    Runs inside one skills pass (joins the caller's): the plugin ownership map, the
    bundled folder and per-pack stat passes are read once, not once per pack.
    """
    with shared_pass_memo():
        return _deploy_skill_folders(skills_root)


def _deploy_skill_folders(skills_root: Path) -> list[str]:
    logs: list[str] = []
    pack_ids = _discovery.list_pack_ids()
    try:
        skills_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return [f"Skill deploy skipped ({skills_root}): {e}"]
    ledger = _read_ledger_doc(skills_root)
    previously_deployed = _ledger_packs(ledger)
    old_stamps = _ledger_stamps(ledger)
    stamps: dict[str, dict] = {}
    deployed: list[str] = []
    for pack_id in pack_ids:
        # Same text for every root: built once per pass, not once per root.
        skill_md = pass_cached(("deploy_md", pack_id), lambda: _deployed_skill_md(pack_id))
        if skill_md is None:
            continue
        dest = skills_root / pack_id
        src_dir = pass_cached(("deploy_src", pack_id), lambda: _pack_source_dir(pack_id))
        refs_src = (src_dir / REFERENCES_DIR) if src_dir else None
        src_key = pass_cached(("deploy_key", pack_id), lambda: _source_key(skill_md, refs_src))
        state = _dest_state(dest)
        # Unchanged since our last deploy: same source, and nobody touched the folder.
        if state and old_stamps.get(pack_id) == {"src": src_key, "dest": state}:
            stamps[pack_id] = old_stamps[pack_id]
            deployed.append(pack_id)
            continue
        # Decide ownership BEFORE writing our own SKILL.md, or the check is
        # circular: a folder a user authored must keep its own extra files.
        owned = (
            not dest.exists()
            or pack_id in previously_deployed
            or _is_managed_skill_dir(dest)
        )
        try:
            dest.mkdir(parents=True, exist_ok=True)
            touched = _write_if_changed(
                dest / PACK_FILE, skill_md.encode("utf-8")
            )
            touched |= _sync_references(refs_src, dest / REFERENCES_DIR)
            if owned:
                for name in _prune_pack_root(dest):
                    touched = True
                    logs.append(f"Stale skill file pruned -> {dest / name}")
            if touched:
                logs.append(f"Skill folder -> {dest}")
            deployed.append(pack_id)
            stamps[pack_id] = {"src": src_key, "dest": _dest_state(dest)}
        except OSError as e:
            logs.append(f"Skill folder failed ({dest}): {e}")
    try:
        for child in skills_root.iterdir():
            if not child.is_dir() or child.name in pack_ids:
                continue
            # Managed frontmatter OR our ledger — the ledger covers a folder
            # whose SKILL.md was deleted or corrupted since we wrote it.
            if _is_managed_skill_dir(child) or child.name in previously_deployed:
                shutil.rmtree(child, ignore_errors=True)
                logs.append(f"Skill folder pruned -> {child}")
    except OSError:
        pass
    _write_deploy_ledger(skills_root, deployed, stamps)
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
    pack_logs = _seeding.seed_skill_packs(force=force_appdata)
    logs.extend(pack_logs)
    dest = _discovery.appdata_skill_packs_dir()
    if not dest.is_dir():
        logs.append("AppData skill_packs/ not written (bundled packs missing)")
        return logs

    # Gateway plugin skills dirs + every IDE config's sibling skills/ folder.
    skills_roots = set(registered_coding_agent_skills_roots())
    configs = list(ide_config_paths or ())
    # One pass for every root: before this, each root re-read the plugin ownership map
    # and re-stat'd every pack (~8 min of 87% CPU after a boot with ship-newest).
    with pass_memo():
        combined = _skill_index.load_skill_text() if configs else ""
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
    """On MCP/EXE startup: upgrade AppData + refresh skills beside every IDE config path.

    Every automatic sync (startup, plugin install/update/uninstall, Store actions) comes
    through here. A second instance (sandboxed test build, other panel port or profile)
    refreshes only its own AppData packs: on Oct 16 a sandbox on port 4300 rewrote the
    person's ~/.claude, ~/.codex and ~/.cursor skill folders with its own packs on
    every plugin load. Settings → Apply (``sync_skill_for_ide``) still writes.
    """
    from frontend.ide_apply import auto_apply_skip_reason

    reason = auto_apply_skip_reason()
    if reason:
        return [*_seeding.seed_skill_packs(force=force_appdata), f"IDE skill folders left alone: {reason}"]
    return sync_skill(
        ide_config_paths=ide_config_paths(antigravity_override),
        force_appdata=force_appdata,
    )


def sync_skill_on_mcp_update(antigravity_override: str = "") -> list[str]:
    """Called when the MCP server or panel starts — same as ``sync_skill_all_ides``."""
    return sync_skill_all_ides(antigravity_override)
