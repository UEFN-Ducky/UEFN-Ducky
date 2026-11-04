"""Export a skill pack to a ``.ducky-skill-pack`` zip and import one back (Store or file)."""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.skills.discovery import (
    _is_bundled_pack_id,
    _is_pack_dir,
    appdata_skill_packs_dir,
    normalize_pack_id,
)
from backend.skills.frontmatter import (
    _is_user_origin_file,
    _license_text_for_pack,
    _meta_map,
    _stamp_ref_origin,
    parse_frontmatter,
    serialize_frontmatter,
)
from backend.skills.layout import (
    EXPORT_FORMAT,
    EXPORT_FORMAT_VERSION,
    EXPORT_META_FILE,
    LICENSE_FILE,
    ORIGIN_STORE,
    PACK_FILE,
    REFERENCES_DIR,
    SOURCE_IMPORT,
    SOURCE_STORE,
)
from backend.skills.manifest import load_pack_manifest
from backend.skills.ownership import plugin_owner_for_skill
from backend.skills.revision import _skills_changed
from backend.skills.seeding import _materialize_pack_to_appdata
from backend.skills.versions import skill_version, skill_version_key


def validate_skill_pack_archive(zip_path: Path) -> dict[str, Any]:
    if not zip_path.is_file():
        raise FileNotFoundError(f"Archive not found: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        export_meta = None
        if EXPORT_META_FILE in names:
            try:
                export_meta = json.loads(zf.read(EXPORT_META_FILE).decode("utf-8"))
            except json.JSONDecodeError:
                export_meta = None
        if PACK_FILE in names:
            meta, _ = parse_frontmatter(zf.read(PACK_FILE).decode("utf-8"))
            md = _meta_map(meta)
            pack_id = str(
                (export_meta or {}).get("pack_id") or meta.get("name") or zip_path.stem
            )
            return {
                "format": "skill",
                "pack_id": pack_id,
                "label": str(md.get("label") or meta.get("name") or pack_id),
                "files": [
                    n
                    for n in names
                    if n == PACK_FILE
                    or (n.startswith(f"{REFERENCES_DIR}/") and n.endswith(".md"))
                ],
                "export_meta": export_meta,
            }
        raise ValueError("Invalid archive: missing SKILL.md")


def export_skill_pack_to_zip(pack_id: str, dest_path: Path) -> Path:
    pid = normalize_pack_id(pack_id)
    owner = plugin_owner_for_skill(pid)
    if owner:
        raise PermissionError(
            f"Cannot export plugin-owned skill {pid!r} — owned by plugin {owner!r}"
        )
    _materialize_pack_to_appdata(pid)
    src = appdata_skill_packs_dir() / pid
    if not (src / PACK_FILE).is_file():
        raise FileNotFoundError(f"Pack not found: {pid}")
    manifest = load_pack_manifest(pid) or {}
    dest_path = Path(dest_path)
    if dest_path.suffix.lower() not in (".zip", ".ducky-skill-pack"):
        dest_path = dest_path.with_suffix(".ducky-skill-pack")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    export_meta: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pack_id": pid,
        "label": str(manifest.get("label") or pid),
        "description": str(manifest.get("description") or ""),
        "version": skill_version(manifest.get("version")),
        "license": str(manifest.get("license") or ""),
        "author": str(manifest.get("author") or ""),
        "copyright": str(manifest.get("copyright") or ""),
        "homepage": str(manifest.get("homepage") or ""),
        "contact": str(manifest.get("contact") or ""),
        "allow_redistribute": bool(manifest.get("allow_redistribute", False)),
    }
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(EXPORT_META_FILE, json.dumps(export_meta, indent=2) + "\n")
        zf.writestr(LICENSE_FILE, _license_text_for_pack({**manifest, "id": pid}))
        zf.write(src / PACK_FILE, PACK_FILE)
        refs_dir = src / REFERENCES_DIR
        if refs_dir.is_dir():
            for md in sorted(refs_dir.glob("*.md")):
                zf.write(md, f"{REFERENCES_DIR}/{md.name}")
        # Store catalog icons (plugin-uefn-ducky-store extracts these from the zip).
        for rel in ("icon.png", "icon.svg", "assets/icon.png", "assets/icon.svg"):
            icon = src / rel
            if icon.is_file():
                zf.write(icon, rel.replace("\\", "/"))
    return dest_path


def export_skill_pack_bytes(pack_id: str) -> dict[str, Any]:
    """Zip pack to memory for browser download."""
    import base64
    import tempfile

    pid = normalize_pack_id(pack_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ducky-skill-pack") as tmp:
        tmp_path = Path(tmp.name)
    try:
        export_skill_pack_to_zip(pid, tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return {
        "pack_id": pid,
        "filename": f"{pid}.ducky-skill-pack",
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def import_skill_pack_from_bytes(
    data: bytes,
    *,
    pack_id: str | None = None,
    replace: bool = False,
    source: str | None = None,
    store_slug: str | None = None,
    store_version: str | int | None = None,
) -> dict[str, Any]:
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ducky-skill-pack") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        return import_skill_pack_from_zip(
            tmp_path,
            pack_id=pack_id,
            replace=replace,
            source=source,
            store_slug=store_slug,
            store_version=store_version,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def _apply_export_meta_commercial(meta: dict[str, Any], export_meta: dict[str, Any] | None) -> None:
    """Fill missing commercial fields from export.json (v2/v3 imports)."""
    if not export_meta:
        return
    md = meta.setdefault("metadata", {})
    if not isinstance(md, dict):
        md = {}
        meta["metadata"] = md
    if not meta.get("license") and export_meta.get("license"):
        meta["license"] = str(export_meta["license"])
    for key in ("author", "copyright", "homepage", "contact"):
        if not md.get(key) and export_meta.get(key):
            md[key] = str(export_meta[key])
    if "allow_redistribute" not in md and "allow_redistribute" in export_meta:
        md["allow_redistribute"] = bool(export_meta["allow_redistribute"])
    # Store bumps often land in export.json while SKILL.md lags — local catalog
    # reads metadata.version, so keep the higher of the two.
    try:
        export_ver = skill_version(export_meta.get("version"))
    except (TypeError, ValueError):
        export_ver = 0
    try:
        cur_ver = skill_version(md.get("version"))
    except (TypeError, ValueError):
        cur_ver = 0
    if skill_version_key(export_ver) > skill_version_key(cur_ver):
        md["version"] = export_ver


def import_skill_pack_from_zip(
    src_path: Path,
    *,
    pack_id: str | None = None,
    replace: bool = False,
    source: str | None = None,
    store_slug: str | None = None,
    store_version: str | int | None = None,
) -> dict[str, Any]:
    src_path = Path(src_path)
    info = validate_skill_pack_archive(src_path)
    target_id = normalize_pack_id(pack_id or str(info["pack_id"]))
    dest = appdata_skill_packs_dir() / target_id
    exists = dest.is_dir() and _is_pack_dir(dest)
    if exists and not replace:
        return {
            "ok": False,
            "conflict": True,
            "existing_id": target_id,
            "label": info["label"],
        }
    if _is_bundled_pack_id(target_id):
        return {
            "ok": False,
            "conflict": True,
            "existing_id": target_id,
            "error": "Cannot replace bundled pack — choose a different id",
        }
    owner = plugin_owner_for_skill(target_id)
    src_kind = (source or "").strip().lower() or SOURCE_IMPORT
    if owner:
        # Store Update All used to hard-fail here when a skill pack and a
        # desktop plugin both claimed the same id. Plugin ownership wins:
        # Store installs become a no-op success; local/import still errors.
        if src_kind == SOURCE_STORE:
            return {
                "ok": True,
                "skipped": True,
                "pack_id": target_id,
                "owned_by_plugin": owner,
                "message": f"Skill {target_id!r} is provided by plugin {owner!r}",
            }
        return {
            "ok": False,
            "conflict": True,
            "existing_id": target_id,
            "error": f"Skill {target_id!r} is owned by plugin {owner!r} — uninstall that plugin first",
        }
    slug = (store_slug or "").strip() or (target_id if src_kind == SOURCE_STORE else "")
    ref_origin = ORIGIN_STORE
    # Fresh install may wipe; updates merge so user-created refs survive.
    if exists and replace:
        dest.mkdir(parents=True, exist_ok=True)
    else:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
    export_meta = info.get("export_meta") if isinstance(info.get("export_meta"), dict) else None
    store_ver: int | str | None = None
    if store_version is not None and str(store_version).strip():
        try:
            # Preserve semantic versions supplied by Store skill packs.
            store_ver = skill_version(store_version)
        except ValueError:
            store_ver = None
    with zipfile.ZipFile(src_path, "r") as zf:
        for name in info["files"]:
            if name == PACK_FILE:
                meta, body = parse_frontmatter(zf.read(name).decode("utf-8"))
                meta["name"] = target_id
                _apply_export_meta_commercial(meta, export_meta)
                md = meta.setdefault("metadata", {})
                if not isinstance(md, dict):
                    md = {}
                    meta["metadata"] = md
                if store_ver is not None:
                    try:
                        cur = skill_version(md.get("version"))
                    except (TypeError, ValueError):
                        cur = 0
                    if skill_version_key(store_ver) > skill_version_key(cur):
                        md["version"] = store_ver
                md["source"] = src_kind
                if slug:
                    md["store_slug"] = slug
                if src_kind == SOURCE_STORE:
                    md.pop("origin", None)  # pack is store-owned; user origin is per-ref only
                (dest / PACK_FILE).write_text(
                    serialize_frontmatter(meta, body), encoding="utf-8"
                )
            else:
                out = dest / REFERENCES_DIR / Path(name).name
                if out.is_file() and _is_user_origin_file(out):
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(_stamp_ref_origin(zf.read(name), ref_origin), encoding="utf-8")
        # Optional LICENSE.txt is informational in the archive; frontmatter is source of truth.
        if LICENSE_FILE in zf.namelist():
            try:
                (dest / LICENSE_FILE).write_bytes(zf.read(LICENSE_FILE))
            except OSError:
                pass
    manifest = load_pack_manifest(target_id) or {}
    result: dict[str, Any] = {
        "ok": True,
        "pack_id": target_id,
        "path": str(dest),
        "label": str(manifest.get("label") or target_id),
    }
    if "allow_redistribute" in manifest:
        result["allow_redistribute"] = bool(manifest["allow_redistribute"])
    if manifest.get("license"):
        result["license"] = str(manifest["license"])
    if manifest.get("author"):
        result["author"] = str(manifest["author"])
    if manifest.get("store_slug"):
        result["store_slug"] = str(manifest["store_slug"])
    if manifest.get("source"):
        result["source"] = str(manifest["source"])
    _skills_changed()
    return result
