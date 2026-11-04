"""Create, edit and delete skill packs and their subskills (the Skills studio)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from backend.skills.discovery import (
    _is_bundled_pack_id,
    appdata_skill_packs_dir,
    bundled_skill_packs_dir,
    normalize_pack_id,
    normalize_subskill_id,
)
from backend.skills.frontmatter import parse_frontmatter, serialize_frontmatter
from backend.skills.layout import (
    CORE_ID,
    DEFAULT_COMMERCIAL_LICENSE,
    ORIGIN_USER,
    PACK_FILE,
    REFERENCES_DIR,
)
from backend.skills.manifest import list_subskills, load_pack_manifest, read_subskill_body
from backend.skills.ownership import plugin_owner_for_skill
from backend.skills.revision import _skills_changed
from backend.skills.seeding import (
    _copy_pack_tree,
    _materialize_pack_to_appdata,
    _write_pack_license_file,
)
from backend.skills.versions import next_skill_version, skill_version


def _appdata_pack_file(pack_id: str) -> Path:
    pid = normalize_pack_id(pack_id)
    _materialize_pack_to_appdata(pid)
    path = appdata_skill_packs_dir() / pid / PACK_FILE
    if not path.is_file():
        raise FileNotFoundError(f"Pack not found: {pid}")
    return path


def _bump_pack_version(pack_id: str) -> Path:
    path = _appdata_pack_file(pack_id)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    md = meta.setdefault("metadata", {})
    if not isinstance(md, dict):
        md = {}
        meta["metadata"] = md
    md["version"] = next_skill_version(md.get("version"))
    path.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
    _skills_changed()
    return path


# --- CRUD ---


def create_skill_pack(pack_id: str, label: str, description: str = "") -> Path:
    pid = normalize_pack_id(pack_id)
    dest = appdata_skill_packs_dir() / pid
    if dest.exists():
        raise FileExistsError(f"Pack already exists: {pid}")
    clean_label = label.strip() or pid.replace("_", " ").title()
    meta = {
        "name": pid,
        "description": description.strip() or clean_label,
        "license": DEFAULT_COMMERCIAL_LICENSE,
        "metadata": {
            "label": clean_label,
            "version": 1,
            "allow_redistribute": False,
            "origin": ORIGIN_USER,
        },
    }
    (dest / REFERENCES_DIR).mkdir(parents=True)
    (dest / PACK_FILE).write_text(
        serialize_frontmatter(meta, f"# {clean_label}\n\nAdd operator guidance for the agent here.\n"),
        encoding="utf-8",
    )
    _skills_changed()
    return dest


def delete_skill_pack(pack_id: str) -> bool:
    pid = normalize_pack_id(pack_id)
    if _is_bundled_pack_id(pid):
        raise ValueError("Cannot delete bundled pack — use reset_skill_pack")
    owner = plugin_owner_for_skill(pid)
    if owner:
        raise ValueError(
            f"Cannot delete plugin-owned skill {pid!r} — uninstall plugin {owner!r} instead"
        )
    dest = appdata_skill_packs_dir() / pid
    if dest.is_dir():
        shutil.rmtree(dest)
        _skills_changed()
        return True
    return False


def reset_skill_pack(pack_id: str) -> Path | None:
    pid = normalize_pack_id(pack_id)
    bundled = bundled_skill_packs_dir()
    if not bundled:
        return None
    src = bundled / pid
    if not src.is_dir() or not (src / PACK_FILE).is_file():
        raise ValueError(f"No bundled pack: {pid}")
    dest = appdata_skill_packs_dir() / pid
    _copy_pack_tree(src, dest)
    _skills_changed()
    return dest


def _reference_path(pack_id: str, subskill_id: str) -> Path:
    return appdata_skill_packs_dir() / pack_id / REFERENCES_DIR / f"{subskill_id}.md"


def create_subskill(
    pack_id: str,
    subskill_id: str,
    label: str,
    description: str = "",
    *,
    load_condition: str = "",
) -> Path:
    pid = normalize_pack_id(pack_id)
    sid = normalize_subskill_id(subskill_id)
    _appdata_pack_file(pid)
    if sid == CORE_ID:
        raise FileExistsError(f"Subskill already exists: {sid}")
    dest = _reference_path(pid, sid)
    if dest.is_file():
        raise FileExistsError(f"Subskill already exists: {sid}")
    clean_label = label.strip() or sid.replace("_", " ").title()
    meta: dict[str, Any] = {
        "description": description.strip() or clean_label,
        "metadata": {
            "label": clean_label,
            "default_enabled": False,
            "origin": ORIGIN_USER,
        },
    }
    if load_condition.strip():
        meta["metadata"]["load_condition"] = load_condition.strip()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(serialize_frontmatter(meta, f"# {clean_label}\n"), encoding="utf-8")
    _bump_pack_version(pid)
    return dest


def create_skill_node(
    pack_id: str,
    subskill_id: str,
    label: str,
    description: str = "",
    *,
    load_condition: str = "",
) -> Path:
    return create_subskill(
        pack_id, subskill_id, label, description, load_condition=load_condition
    )


def delete_subskill(pack_id: str, subskill_id: str) -> bool:
    pid = normalize_pack_id(pack_id)
    sid = normalize_subskill_id(subskill_id)
    if sid == CORE_ID:
        raise ValueError("Cannot delete SKILL.md — delete or reset the pack instead")
    _appdata_pack_file(pid)
    path = _reference_path(pid, sid)
    if not path.is_file():
        return False
    path.unlink()
    _bump_pack_version(pid)
    return True


def save_subskill(pack_id: str, subskill_id: str, text: str) -> Path:
    """Replace a file's markdown body, preserving its frontmatter."""
    pid = normalize_pack_id(pack_id)
    sid = normalize_subskill_id(subskill_id)
    if sid == CORE_ID:
        path = _appdata_pack_file(pid)
    else:
        _appdata_pack_file(pid)
        path = _reference_path(pid, sid)
        if not path.is_file():
            raise FileNotFoundError(f"Subskill not found: {sid}")
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    if meta:
        path.write_text(serialize_frontmatter(meta, text), encoding="utf-8")
    else:
        path.write_text(text if text.endswith("\n") else f"{text.rstrip()}\n", encoding="utf-8")
    _skills_changed()
    return path


def save_skill_node(pack_id: str, node_id: str, patch: dict[str, Any]) -> Path:
    """Patch a file's frontmatter metadata (label, description, toggles)."""
    pid = normalize_pack_id(pack_id)
    sid = normalize_subskill_id(node_id)
    if sid == CORE_ID:
        path = _appdata_pack_file(pid)
    else:
        _appdata_pack_file(pid)
        path = _reference_path(pid, sid)
        if not path.is_file():
            raise FileNotFoundError(f"Subskill not found: {sid}")
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    md = meta.setdefault("metadata", {})
    if not isinstance(md, dict):
        md = {}
        meta["metadata"] = md
    if "label" in patch and isinstance(patch["label"], str):
        md["label"] = patch["label"]
    if "description" in patch and isinstance(patch["description"], str):
        meta["description"] = patch["description"]
    if sid != CORE_ID:
        if "default_enabled" in patch:
            md["default_enabled"] = bool(patch["default_enabled"])
        if "load_condition" in patch:
            cond = patch["load_condition"]
            if cond is None or cond == "":
                md.pop("load_condition", None)
            elif isinstance(cond, str):
                md["load_condition"] = cond.strip()
    path.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
    return _bump_pack_version(pid)


def save_pack_manifest(pack_id: str, patch: dict[str, Any]) -> Path:
    pid = normalize_pack_id(pack_id)
    owner = plugin_owner_for_skill(pid)
    if owner:
        raise PermissionError(
            f"Cannot edit plugin-owned skill {pid!r} — owned by plugin {owner!r}"
        )
    path = _appdata_pack_file(pid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    md = meta.setdefault("metadata", {})
    if not isinstance(md, dict):
        md = {}
        meta["metadata"] = md
    if "label" in patch and isinstance(patch["label"], str):
        md["label"] = patch["label"]
    if "description" in patch and isinstance(patch["description"], str):
        meta["description"] = patch["description"]
    if "license" in patch:
        lic = patch["license"]
        if lic is None or (isinstance(lic, str) and not lic.strip()):
            meta.pop("license", None)
        elif isinstance(lic, str):
            meta["license"] = lic.strip()
    for key in ("author", "copyright", "homepage", "contact"):
        if key not in patch:
            continue
        val = patch[key]
        if val is None or (isinstance(val, str) and not val.strip()):
            md.pop(key, None)
        elif isinstance(val, str):
            md[key] = val.strip()
    if "allow_redistribute" in patch:
        md["allow_redistribute"] = bool(patch["allow_redistribute"])
    path.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
    bumped = _bump_pack_version(pid)
    _write_pack_license_file(pid)
    return bumped


def save_skill_layout(pack_id: str, layout: dict[str, Any]) -> Path:
    """Deprecated — the graph editor is gone; kept as a no-op for old callers."""
    del layout
    return _appdata_pack_file(normalize_pack_id(pack_id))


# --- File listing for the studio UI ---


def get_skill_pack_files(pack_id: str) -> dict[str, Any]:
    pid = normalize_pack_id(pack_id)
    manifest = load_pack_manifest(pid)
    if not manifest:
        raise FileNotFoundError(f"Pack not found: {pid}")
    files: list[dict[str, Any]] = []
    for sub in list_subskills(manifest):
        sid = str(sub.get("id", ""))
        files.append(
            {
                "id": sid,
                "file": str(sub.get("file") or f"{sid}.md"),
                "label": str(sub.get("label") or sid),
                "description": str(sub.get("description") or ""),
                "default_enabled": bool(sub.get("default_enabled", False)),
                "always_on": bool(sub.get("always_on", False)),
                "load_condition": str(sub.get("load_condition") or "") or None,
                "text": read_subskill_body(pid, sid) or "",
            }
        )
    return {
        "pack_id": pid,
        "label": str(manifest.get("label") or pid),
        "description": str(manifest.get("description") or ""),
        "kind": str(manifest.get("kind") or "custom"),
        "version": skill_version(manifest.get("version")),
        "path": str(appdata_skill_packs_dir() / pid),
        "files": files,
    }


def get_skill_pack_graph(pack_id: str) -> dict[str, Any]:
    """Legacy shape: same data with files also exposed as flat nodes."""
    info = get_skill_pack_files(pack_id)
    nodes = [
        {**f, "parent_id": None, "type": "skill", "x": 0.0, "y": 0.0} for f in info["files"]
    ]
    return {**info, "layout": {}, "nodes": nodes}
