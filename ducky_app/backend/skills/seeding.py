"""Seed the bundled skill packs into AppData, merging updates without losing user files."""

from __future__ import annotations

import shutil
from pathlib import Path

from backend.skills.discovery import (
    _is_pack_dir,
    appdata_skill_packs_dir,
    bundled_skill_packs_dir,
    normalize_pack_id,
)
from backend.skills.frontmatter import (
    _is_user_origin_file,
    _license_text_for_pack,
    _meta_map,
    _origin_from_meta,
    _read,
    _stamp_ref_origin,
    parse_frontmatter,
)
from backend.skills.layout import LICENSE_FILE, ORIGIN_SHIPPED, PACK_FILE, REFERENCES_DIR
from backend.skills.manifest import load_pack_manifest
from backend.skills.revision import _skills_changed
from backend.skills.versions import skill_version, skill_version_key


def _copy_pack_tree(src: Path, dest: Path) -> None:
    """Full replace (reset / first install). Prefer :func:`_merge_pack_tree` for updates."""
    if dest.exists():
        shutil.rmtree(dest)
    # Another seeding pass in this process can create ``dest`` between the two steps
    # (the release suite caught FileExistsError); every shipped file is still copied over.
    shutil.copytree(src, dest, dirs_exist_ok=True)


def _merge_pack_tree(src: Path, dest: Path, *, ref_origin: str = ORIGIN_SHIPPED) -> None:
    """Update SKILL.md + shipped refs; never delete or overwrite ``origin: user`` refs.

    A ref the source no longer ships is deleted **only** when its own stamp
    matches ``ref_origin`` — i.e. this same source put it there. Without that,
    a subskill dropped in a new build survived in AppData forever, and since
    AppData outranks plugin-owned content in ``_pack_roots`` it would be
    redeployed to every IDE — resurrecting content that was removed on purpose.
    ``user`` / ``store`` / unstamped refs are never touched.
    """
    dest.mkdir(parents=True, exist_ok=True)
    skill_src = src / PACK_FILE
    if skill_src.is_file():
        shutil.copy2(skill_src, dest / PACK_FILE)
    license_src = src / LICENSE_FILE
    if license_src.is_file():
        try:
            shutil.copy2(license_src, dest / LICENSE_FILE)
        except OSError:
            pass
    refs_src = src / REFERENCES_DIR
    refs_dest = dest / REFERENCES_DIR
    refs_dest.mkdir(parents=True, exist_ok=True)
    if not refs_src.is_dir():
        return
    shipped_names: set[str] = set()
    for md in sorted(refs_src.glob("*.md")):
        out = refs_dest / md.name
        shipped_names.add(md.name)
        if out.is_file() and _is_user_origin_file(out):
            continue
        text = _stamp_ref_origin(md.read_text(encoding="utf-8"), ref_origin)
        out.write_text(text, encoding="utf-8")
    # Prune our own orphans: refs this source stamped but no longer ships.
    for old in sorted(refs_dest.glob("*.md")):
        if old.name in shipped_names:
            continue
        meta, _ = parse_frontmatter(_read(old) or "")
        if _origin_from_meta(meta) != ref_origin:
            continue  # user / store / unstamped — not ours to delete
        try:
            old.unlink()
        except OSError:
            pass


def _pack_version(root: Path) -> int | str:
    raw = _read(root / PACK_FILE)
    if raw is not None:
        meta, _ = parse_frontmatter(raw)
        return skill_version(_meta_map(meta).get("version"))
    return 0


SEED_MARKER_FILE = ".seed_app_version"


def _current_app_version() -> str:
    """Running control-panel build version, or '' when it can't be determined."""
    try:
        from frontend import __version__

        return str(__version__).strip()
    except Exception:
        return ""


def _seed_marker_path() -> Path:
    # A dotfile beside the packs — ignored by list_pack_ids() and the seed loop
    # (both skip non-directories), and never deployed to IDE skill folders.
    return appdata_skill_packs_dir() / SEED_MARKER_FILE


def _last_seeded_app_version() -> str:
    return (_read(_seed_marker_path()) or "").strip()


def _write_seeded_app_version(version: str) -> None:
    try:
        path = _seed_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version, encoding="utf-8")
    except OSError:
        pass


def _write_pack_license_file(pack_id: str) -> None:
    """Write LICENSE.txt beside SKILL.md in AppData (for Explorer / export prep)."""
    pid = normalize_pack_id(pack_id)
    dest = appdata_skill_packs_dir() / pid
    if not (dest / PACK_FILE).is_file():
        return
    manifest = load_pack_manifest(pid) or {"id": pid, "label": pid}
    text = _license_text_for_pack({**manifest, "id": pid})
    path = dest / LICENSE_FILE
    try:
        # Unchanged text is not rewritten: every seed (each skill ship) wrote it again, a
        # disk write and an antivirus scan per pack for byte-identical text.
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass


# Skill packs that now ship inside a desktop plugin (not frontend/skill_packs/).
# Drop leftover AppData copies so Store install of that plugin is not blocked.
_MOVED_TO_PLUGIN_SKILLS = frozenset({"animation", "datatables", "islandsettings", "leveldesign", "materials", "modeling", "scenegraph", "tester", "uefn", "verse", "vfx"})


def seed_skill_packs(*, force: bool = False) -> list[str]:
    """Seed bundled packs into AppData. Returns log lines."""
    logs: list[str] = []
    dest_root = appdata_skill_packs_dir()
    dest_root.mkdir(parents=True, exist_ok=True)

    app_version = _current_app_version()
    version_changed = bool(app_version) and app_version != _last_seeded_app_version()
    if version_changed and not force:
        force = True
        logs.append(f"App version {app_version} changed since last seed — refreshing shipped packs")

    bundled = bundled_skill_packs_dir()
    if not bundled or not bundled.is_dir():
        logs.append("Bundled skill_packs/ not found")
        return logs
    bundled_ids: set[str] = set()
    for src_pack in sorted(bundled.iterdir()):
        if not src_pack.is_dir() or not (src_pack / PACK_FILE).is_file():
            continue
        pack_id = src_pack.name
        bundled_ids.add(pack_id)
        dest_pack = dest_root / pack_id
        bundled_ver = _pack_version(src_pack)
        dest_ver = _pack_version(dest_pack) if dest_pack.is_dir() else 0
        # Only touch shipped pack ids. Custom AppData packs (not in the bundle) are never
        # listed here, so Skills-studio / user packs are never deleted or overwritten.
        if dest_pack.is_dir() and skill_version_key(dest_ver) > skill_version_key(bundled_ver):
            logs.append(
                f"Skill pack {pack_id} v{dest_ver} kept (newer than bundled v{bundled_ver})"
            )
            _write_pack_license_file(pack_id)
            continue
        if dest_pack.is_dir() and not force and skill_version_key(dest_ver) >= skill_version_key(bundled_ver):
            _write_pack_license_file(pack_id)
            continue
        if dest_pack.is_dir() and _is_pack_dir(dest_pack):
            _merge_pack_tree(src_pack, dest_pack, ref_origin=ORIGIN_SHIPPED)
            logs.append(f"Skill pack {pack_id} v{bundled_ver} merged -> {dest_pack}")
        else:
            _copy_pack_tree(src_pack, dest_pack)
            # Stamp shipped refs so later Store/seed merges can tell them apart from yours.
            refs = dest_pack / REFERENCES_DIR
            if refs.is_dir():
                for md in refs.glob("*.md"):
                    if not _is_user_origin_file(md):
                        md.write_text(
                            _stamp_ref_origin(md.read_text(encoding="utf-8"), ORIGIN_SHIPPED),
                            encoding="utf-8",
                        )
            logs.append(f"Skill pack {pack_id} v{bundled_ver} seeded -> {dest_pack}")
        _write_pack_license_file(pack_id)

    for pack_id in sorted(_MOVED_TO_PLUGIN_SKILLS):
        if pack_id in bundled_ids:
            continue
        dest_pack = dest_root / pack_id
        if dest_pack.is_dir():
            shutil.rmtree(dest_pack, ignore_errors=True)
            logs.append(f"Removed skill pack {pack_id} (now ships in a desktop plugin)")

    if version_changed:
        _write_seeded_app_version(app_version)
    if any(("seeded ->" in line) or ("merged ->" in line) or line.startswith("Removed skill pack") for line in logs):
        _skills_changed()
    return logs


def _materialize_pack_to_appdata(pack_id: str) -> Path | None:
    """Copy a bundled pack into AppData when missing so edits can persist."""
    pid = normalize_pack_id(pack_id)
    dest = appdata_skill_packs_dir() / pid
    if (dest / PACK_FILE).is_file():
        _write_pack_license_file(pid)
        return dest
    bundled = bundled_skill_packs_dir()
    if not bundled:
        return None
    src = bundled / pid
    if not src.is_dir() or not (src / PACK_FILE).is_file():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack_tree(src, dest)
    _write_pack_license_file(pid)
    return dest
