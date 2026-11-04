"""A pack's manifest (derived from SKILL.md + references), its cache, and subskill bodies."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.skills.discovery import (
    _is_bundled_pack_id,
    _pack_roots,
    _reference_files,
    appdata_skill_packs_dir,
    list_pack_ids,
)
from backend.skills.frontmatter import (
    _commercial_fields_from_meta,
    _meta_map,
    _read,
    parse_frontmatter,
)
from backend.skills.layout import (
    CORE_ID,
    DEFAULT_COMMERCIAL_LICENSE,
    ORIGIN_PLUGIN,
    ORIGIN_SHIPPED,
    ORIGIN_STORE,
    ORIGIN_USER,
    PACK_FILE,
    REFERENCES_DIR,
    SOURCE_STORE,
)
from backend.skills.ownership import _is_plugin_owned_pack_id, plugin_owner_for_skill
from backend.skills.passes import _PASS_MEMO
from backend.skills.versions import skill_version


def _manifest_from_skill_dir(root: Path, pack_id: str, kind: str) -> dict[str, Any]:
    meta, _ = parse_frontmatter(_read(root / PACK_FILE) or "")
    md = _meta_map(meta)
    label = str(md.get("label") or meta.get("name") or pack_id)
    pack_origin = str(md.get("origin") or "").strip().lower()
    core_origin = pack_origin or (
        ORIGIN_PLUGIN
        if kind == "plugin"
        else ORIGIN_SHIPPED
        if kind == "bundled"
        else ORIGIN_STORE
        if kind == "store" or str(md.get("source") or "") == SOURCE_STORE
        else ORIGIN_USER
    )
    manifest: dict[str, Any] = {
        "id": pack_id,
        "label": label,
        "version": skill_version(md.get("version")),
        "kind": kind,
        "description": str(meta.get("description") or ""),
        "subskills": [
            {
                "id": CORE_ID,
                "file": PACK_FILE,
                "label": "Core",
                "description": str(meta.get("description") or ""),
                "default_enabled": True,
                "always_on": True,
                "origin": core_origin,
            }
        ],
    }
    manifest.update(_commercial_fields_from_meta(meta))
    for ref in _reference_files(root):
        rmeta, _ = parse_frontmatter(_read(ref) or "")
        rmd = _meta_map(rmeta)
        entry: dict[str, Any] = {
            "id": ref.stem,
            "file": f"{REFERENCES_DIR}/{ref.name}",
            "label": str(rmd.get("label") or rmeta.get("name") or ref.stem),
            "description": str(rmeta.get("description") or ""),
            "default_enabled": bool(rmd.get("default_enabled", False)),
        }
        if "always_on" in rmd:
            entry["always_on"] = bool(rmd["always_on"])
        if rmd.get("load_condition"):
            entry["load_condition"] = str(rmd["load_condition"])
        ref_origin = str(rmd.get("origin") or "").strip().lower()
        if ref_origin:
            entry["origin"] = ref_origin
        manifest["subskills"].append(entry)
    return manifest


def _pack_stamp(root: Path) -> str:
    """One stat pass over SKILL.md + references/*.md (what parsing would read)."""
    memo = _PASS_MEMO.get()
    if memo is not None:
        key = ("stamp", str(root))
        if key not in memo:
            memo[key] = _pack_stamp_uncached(root)
        return memo[key]
    return _pack_stamp_uncached(root)


def _pack_stamp_uncached(root: Path) -> str:
    parts: list[str] = []
    refs = sorted((root / REFERENCES_DIR).glob("*.md")) if (root / REFERENCES_DIR).is_dir() else []
    for f in [root / PACK_FILE, *refs]:
        try:
            st = f.stat()
        except OSError:
            continue
        parts.append(f"{f.name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def _manifest_cache_get(pack_id: str, root: Path, kind: str) -> dict[str, Any] | None:
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if not use_db("cache_docs"):
            return None
        doc = kv.get_doc("cache_docs", f"skill_manifest:{pack_id}")
        if isinstance(doc, dict) and doc.get("stamp") == f"{kind}|{root}|{_pack_stamp(root)}":
            manifest = doc.get("manifest")
            return dict(manifest) if isinstance(manifest, dict) else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _manifest_cache_put(pack_id: str, root: Path, kind: str, manifest: dict[str, Any]) -> None:
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if use_db("cache_docs"):
            kv.set_doc("cache_docs", f"skill_manifest:{pack_id}", {"stamp": f"{kind}|{root}|{_pack_stamp(root)}", "manifest": manifest})
    except Exception:  # noqa: BLE001
        pass


def load_pack_manifest(pack_id: str) -> dict[str, Any] | None:
    if _is_bundled_pack_id(pack_id):
        kind = "bundled"
    elif _is_plugin_owned_pack_id(pack_id):
        kind = "plugin"
    else:
        kind = "custom"
    # The cache is keyed by the kind known before SKILL.md is read: a Store pack
    # is only told apart from a custom one by its frontmatter, which the stamp
    # already covers. Keying it by "store" meant Store packs never hit the cache.
    cache_kind = kind
    for root in _pack_roots(pack_id):
        if (root / PACK_FILE).is_file():
            cached = _manifest_cache_get(pack_id, root, cache_kind)
            if cached is not None:
                return cached
            # Store-installed packs live in AppData as custom until we read source=.
            if kind == "custom":
                meta, _ = parse_frontmatter(_read(root / PACK_FILE) or "")
                md = _meta_map(meta)
                if str(md.get("source") or "").strip() == SOURCE_STORE:
                    kind = "store"
            manifest = _manifest_from_skill_dir(root, pack_id, kind)
            owner = plugin_owner_for_skill(pack_id)
            if owner:
                manifest["source_plugin_id"] = owner
            if kind == "plugin":
                # Plugin-bundled skills are not redistributable unless the pack says so.
                if not str(manifest.get("license") or "").strip():
                    manifest["license"] = DEFAULT_COMMERCIAL_LICENSE
                if "allow_redistribute" not in manifest:
                    manifest["allow_redistribute"] = False
            _manifest_cache_put(pack_id, root, cache_kind, manifest)
            return manifest
    return None


def list_subskills(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("subskills")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict) and s.get("id")]


def subskill_parent_id(sub: dict[str, Any]) -> str | None:
    raw = sub.get("parent_id")
    if raw is None or raw == "":
        return None
    return str(raw)


def list_root_subskills(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in list_subskills(manifest) if subskill_parent_id(s) is None]


def _subskill_rel_path(filename: str) -> str:
    fn = filename.replace("\\", "/")
    if fn == PACK_FILE or "/" in fn:
        return fn
    return f"{REFERENCES_DIR}/{fn}"


def subskill_body_path(pack_id: str, filename: str) -> Path:
    return appdata_skill_packs_dir() / pack_id / _subskill_rel_path(filename)


def read_subskill_body(pack_id: str, subskill_id: str) -> str | None:
    manifest = load_pack_manifest(pack_id)
    if not manifest:
        return None
    for sub in list_subskills(manifest):
        if str(sub.get("id")) == subskill_id:
            rel = _subskill_rel_path(str(sub.get("file") or f"{subskill_id}.md"))
            for root in _pack_roots(str(manifest.get("id") or pack_id)):
                raw = _read(root / rel)
                if raw is not None:
                    _, body = parse_frontmatter(raw)
                    return body
            return None
    return None


def list_skill_packs(*, include_text: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pack_id in list_pack_ids():
        manifest = load_pack_manifest(pack_id)
        if not manifest:
            continue
        entry: dict[str, Any] = {
            "id": pack_id,
            "label": str(manifest.get("label") or pack_id),
            "description": str(manifest.get("description") or ""),
            "kind": str(manifest.get("kind") or "custom"),
            "version": skill_version(manifest.get("version")),
            "path": str(appdata_skill_packs_dir() / pack_id),
            "subskills": [],
        }
        for key in (
            "license",
            "author",
            "copyright",
            "homepage",
            "contact",
            "allow_redistribute",
            "source_plugin_id",
            "source",
            "store_slug",
            "origin",
        ):
            if key in manifest:
                entry[key] = manifest[key]
        for sub in list_subskills(manifest):
            sid = str(sub.get("id", ""))
            fn = str(sub.get("file") or f"{sid}.md")
            sub_entry: dict[str, Any] = {
                "id": sid,
                "label": str(sub.get("label") or sid),
                "description": str(sub.get("description") or ""),
                "default_enabled": bool(sub.get("default_enabled", False)),
                "always_on": bool(sub.get("always_on", False)),
                "parent_id": subskill_parent_id(sub),
                "load_condition": str(sub.get("load_condition") or "") or None,
                "file": fn,
            }
            if sub.get("origin"):
                sub_entry["origin"] = str(sub["origin"])
            if include_text:
                sub_entry["text"] = read_subskill_body(pack_id, sid) or ""
            entry["subskills"].append(sub_entry)
        out.append(entry)
    return out
