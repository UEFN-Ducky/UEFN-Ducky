"""Skill packs in the open Agent Skills layout (SKILL.md + references/*.md).

Each pack is a standard skill folder that works as-is in Claude Code, Cursor,
or any agent that understands the SKILL.md convention:

    skill_packs/<pack_id>/
        SKILL.md            # frontmatter (name, description, metadata) + core guidance
        references/<id>.md  # optional detail files, loaded on demand

Pack metadata lives in SKILL.md frontmatter (``metadata.label`` /
``metadata.version``). Reference metadata (label, default_enabled,
load_condition, order) lives in each reference file's own frontmatter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPORT_FORMAT = "ducky-skill-pack"
EXPORT_FORMAT_VERSION = 3
EXPORT_META_FILE = "export.json"
LICENSE_FILE = "LICENSE.txt"
DEFAULT_COMMERCIAL_LICENSE = "All Rights Reserved"

SKILL_PACKS_DIR = "skill_packs"
PACK_FILE = "SKILL.md"
REFERENCES_DIR = "references"
CORE_ID = "core"

# Provenance stamps in SKILL.md / references frontmatter metadata.
ORIGIN_USER = "user"
ORIGIN_STORE = "store"
ORIGIN_SHIPPED = "shipped"
ORIGIN_PLUGIN = "plugin"
SOURCE_STORE = "store"
SOURCE_IMPORT = "import"

_PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SUBSKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLAIN_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./ -]*$")


@dataclass
class SkillSelection:
    """Deny-list of skill packs + optional per-pack subskill allowlists.

    Empty disabled_packs = every installed pack is available.
    enabled_subskills[pack] missing = all references for that pack; present = only those ids
    (always_on / core still readable).
    """

    disabled_packs: list[str] = field(default_factory=list)
    enabled_subskills: dict[str, list[str]] = field(default_factory=dict)

    @property
    def enabled_packs(self) -> list[str]:
        """Packs available to the agent (all installed minus denied)."""
        denied = set(self.disabled_packs)
        return [pid for pid in list_pack_ids() if pid not in denied]


_ACTIVE_DISABLED_PACKS: ContextVar[frozenset[str] | None] = ContextVar(
    "uefn_ducky_active_disabled_skill_packs", default=None
)
_ACTIVE_ENABLED_SUBSKILLS: ContextVar[dict[str, frozenset[str]] | None] = ContextVar(
    "uefn_ducky_active_enabled_subskills", default=None
)


def set_active_disabled_packs(pack_ids: list[str] | None) -> None:
    """Scope denied skill packs to the current agent run (None = no deny)."""
    if pack_ids is None:
        _ACTIVE_DISABLED_PACKS.set(None)
        return
    _ACTIVE_DISABLED_PACKS.set(frozenset(str(p) for p in pack_ids if str(p).strip()))


def active_disabled_packs() -> frozenset[str]:
    return _ACTIVE_DISABLED_PACKS.get() or frozenset()


def set_active_enabled_subskills(selection: dict[str, list[str]] | None) -> None:
    """Scope per-pack subskill allowlists for the current agent run (None = no filter)."""
    if not selection:
        _ACTIVE_ENABLED_SUBSKILLS.set(None)
        return
    out: dict[str, frozenset[str]] = {}
    for pack_id, ids in selection.items():
        key = str(pack_id or "").strip()
        if not key or not isinstance(ids, list):
            continue
        out[key] = frozenset(str(x).strip() for x in ids if str(x).strip())
    _ACTIVE_ENABLED_SUBSKILLS.set(out or None)


def active_enabled_subskills() -> dict[str, frozenset[str]] | None:
    return _ACTIVE_ENABLED_SUBSKILLS.get()


def appdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky"


def appdata_skill_packs_dir() -> Path:
    return appdata_dir() / SKILL_PACKS_DIR


def bundled_skill_packs_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "frontend" / SKILL_PACKS_DIR
        if p.is_dir():
            return p
    repo = Path(__file__).resolve().parent.parent / "frontend" / SKILL_PACKS_DIR
    if repo.is_dir():
        return repo
    return None


def _read(path: Path) -> str | None:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    raw = _read(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# --- Frontmatter ---
#
# Minimal YAML subset: top-level ``key: value`` scalars plus one-level nested
# maps (``metadata:`` with two-space indented entries). Enough for the Agent
# Skills frontmatter contract without a YAML dependency.


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip('"')
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    try:
        return int(raw)
    except ValueError:
        return raw


def _dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if _PLAIN_SCALAR_RE.match(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter dict, body). Missing/unterminated frontmatter -> ({}, text)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, Any] = {}
    nested: dict[str, Any] | None = None
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1 :]).lstrip("\n")
        if not line.strip():
            continue
        if line.startswith("  ") and nested is not None:
            key, sep, raw = line.strip().partition(":")
            if sep:
                nested[key.strip()] = _parse_scalar(raw)
            continue
        key, sep, raw = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if raw.strip() == "":
            nested = {}
            meta[key] = nested
        else:
            nested = None
            meta[key] = _parse_scalar(raw)
    return {}, text


def serialize_frontmatter(meta: dict[str, Any], body: str) -> str:
    lines = ["---"]
    scalar_keys = [k for k, v in meta.items() if not isinstance(v, dict)]
    # Keep the spec-visible keys first so the file reads like any other skill.
    for key in ("name", "description", "license"):
        if key in scalar_keys:
            lines.append(f"{key}: {_dump_scalar(meta[key])}")
    for key in scalar_keys:
        if key not in ("name", "description", "license"):
            lines.append(f"{key}: {_dump_scalar(meta[key])}")
    for key, value in meta.items():
        if isinstance(value, dict) and value:
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {_dump_scalar(sub_value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def _meta_map(meta: dict[str, Any]) -> dict[str, Any]:
    md = meta.get("metadata")
    return md if isinstance(md, dict) else {}


def _commercial_fields_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Pack publishing / legal fields from SKILL.md frontmatter."""
    md = _meta_map(meta)
    out: dict[str, Any] = {}
    if meta.get("license"):
        out["license"] = str(meta["license"])
    for key in ("author", "copyright", "homepage", "contact"):
        if md.get(key):
            out[key] = str(md[key])
    if "allow_redistribute" in md:
        out["allow_redistribute"] = bool(md["allow_redistribute"])
    if md.get("source"):
        out["source"] = str(md["source"]).strip()
    if md.get("store_slug"):
        out["store_slug"] = str(md["store_slug"]).strip()
    if md.get("origin"):
        out["origin"] = str(md["origin"]).strip()
    return out


def _origin_from_meta(meta: dict[str, Any]) -> str:
    """Per-file origin stamp from frontmatter (``user`` / ``store`` / …)."""
    raw = _meta_map(meta).get("origin")
    return str(raw).strip().lower() if raw else ""


def _is_user_origin_file(path: Path) -> bool:
    """True when a markdown file is tagged ``metadata.origin: user``."""
    if not path.is_file():
        return False
    meta, _ = parse_frontmatter(_read(path) or "")
    return _origin_from_meta(meta) == ORIGIN_USER


def _stamp_ref_origin(raw: bytes | str, origin: str) -> str:
    """Ensure a reference file's frontmatter has ``metadata.origin``."""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    meta, body = parse_frontmatter(text)
    md = meta.setdefault("metadata", {})
    if not isinstance(md, dict):
        md = {}
        meta["metadata"] = md
    md["origin"] = origin
    return serialize_frontmatter(meta, body)


def _pack_origin_tag(manifest: dict[str, Any]) -> str:
    """AI/UI tag for a pack: yours / store / shipped / plugin."""
    kind = str(manifest.get("kind") or "")
    if kind == "plugin":
        return ORIGIN_PLUGIN
    if kind == "bundled":
        return ORIGIN_SHIPPED
    if kind == "store" or str(manifest.get("source") or "") == SOURCE_STORE:
        return ORIGIN_STORE
    if str(manifest.get("origin") or "") == ORIGIN_USER:
        return ORIGIN_USER
    if kind == "custom":
        return ORIGIN_USER
    return kind or ORIGIN_USER


def _subskill_origin_tag(sub: dict[str, Any], pack_tag: str) -> str:
    """AI/UI tag for a subskill; user-created refs win over pack provenance."""
    origin = str(sub.get("origin") or "").strip().lower()
    if origin == ORIGIN_USER:
        return ORIGIN_USER
    if origin in (ORIGIN_STORE, ORIGIN_SHIPPED, ORIGIN_PLUGIN):
        return origin
    if str(sub.get("id") or "") == CORE_ID:
        return pack_tag
    # Untagged refs inherit pack provenance (store/shipped/plugin), not "yours".
    return pack_tag if pack_tag != ORIGIN_USER else ORIGIN_SHIPPED


def _index_tag_label(tag: str) -> str:
    """Bracket label for the AI skill index."""
    if tag == ORIGIN_USER:
        return "yours"
    if tag == ORIGIN_STORE:
        return "store"
    if tag == ORIGIN_SHIPPED:
        return "shipped"
    if tag == ORIGIN_PLUGIN:
        return "plugin"
    return tag or "yours"


def pack_index_tag(manifest: dict[str, Any]) -> str:
    """Public: ``yours`` / ``store`` / ``shipped`` / ``plugin`` for a pack."""
    return _index_tag_label(_pack_origin_tag(manifest))


def subskill_index_tag(sub: dict[str, Any], pack_manifest: dict[str, Any]) -> str:
    """Public: index tag for one subskill under ``pack_manifest``."""
    return _index_tag_label(_subskill_origin_tag(sub, _pack_origin_tag(pack_manifest)))


def _license_text_for_pack(manifest: dict[str, Any]) -> str:
    """Plain-text LICENSE.txt body for zip exports."""
    license_name = str(manifest.get("license") or DEFAULT_COMMERCIAL_LICENSE).strip()
    author = str(manifest.get("author") or "").strip()
    copyright_line = str(manifest.get("copyright") or "").strip()
    allow = bool(manifest.get("allow_redistribute", False))
    homepage = str(manifest.get("homepage") or "").strip()
    contact = str(manifest.get("contact") or "").strip()
    pack_label = str(manifest.get("label") or manifest.get("id") or "this skill pack")

    lines: list[str] = [f"License: {license_name}", ""]
    if copyright_line:
        lines.append(copyright_line)
        lines.append("")
    elif author:
        lines.append(f"Copyright (c) {author}")
        lines.append("")
    if author:
        lines.append(f"Author: {author}")
        lines.append("")
    if homepage:
        lines.append(f"Homepage: {homepage}")
        lines.append("")
    if contact:
        lines.append(f"Contact: {contact}")
        lines.append("")

    if not allow or license_name == DEFAULT_COMMERCIAL_LICENSE:
        lines.extend(
            [
                f'"{pack_label}" is licensed for personal use by the purchaser only.',
                "You may not redistribute, resell, share, sublicense, or publicly host",
                "this skill pack (including the .ducky-skill-pack / .zip archive) without",
                "prior written permission from the copyright holder.",
                "",
                "All rights reserved unless a different license is stated above.",
            ]
        )
    elif license_name.upper() == "MIT":
        lines.extend(
            [
                "Permission is hereby granted, free of charge, to any person obtaining a copy",
                'of this software and associated documentation files (the "Software"), to deal',
                "in the Software without restriction, including without limitation the rights",
                "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell",
                "copies of the Software, subject to the MIT license terms.",
            ]
        )
    else:
        lines.append(
            "See the license named above and the pack author's terms for permitted use."
        )
        if not allow:
            lines.append("Redistribution of this archive is not permitted.")
    return "\n".join(lines).rstrip() + "\n"


# --- Manifest synthesis (derived from SKILL.md + references) ---


def _plugin_owned_skill_map() -> dict[str, Path]:
    """pack_id → skill dir for skills bundled inside *enabled* desktop plugins."""
    try:
        from backend.uefn_plugins.host import is_plugin_enabled
        from backend.uefn_plugins.store import list_plugin_owned_skills

        out: dict[str, Path] = {}
        for entry in list_plugin_owned_skills():
            owner = str(entry.get("plugin_id") or "").strip()
            if owner and not is_plugin_enabled(owner):
                continue
            pack_id = str(entry.get("pack_id") or "").strip()
            path = Path(str(entry.get("path") or ""))
            if pack_id and path.is_dir() and (path / PACK_FILE).is_file():
                out[pack_id] = path
        return out
    except Exception:
        return {}


def plugin_owner_for_skill(pack_id: str) -> str | None:
    """Return owning desktop plugin id when ``pack_id`` is plugin-bundled."""
    try:
        from backend.uefn_plugins.store import list_plugin_owned_skills

        for entry in list_plugin_owned_skills():
            if str(entry.get("pack_id") or "") == pack_id:
                owner = str(entry.get("plugin_id") or "").strip()
                return owner or None
    except Exception:
        return None
    return None


def _pack_roots(pack_id: str) -> list[Path]:
    roots = [appdata_skill_packs_dir() / pack_id]
    plugin_map = _plugin_owned_skill_map()
    if pack_id in plugin_map:
        roots.append(plugin_map[pack_id])
    bundled = bundled_skill_packs_dir()
    if bundled:
        roots.append(bundled / pack_id)
    return roots


def _is_pack_dir(root: Path) -> bool:
    return (root / PACK_FILE).is_file()


def _is_bundled_pack_id(pack_id: str) -> bool:
    bundled = bundled_skill_packs_dir()
    return bool(bundled and _is_pack_dir(bundled / pack_id))


def _is_plugin_owned_pack_id(pack_id: str) -> bool:
    return plugin_owner_for_skill(pack_id) is not None


def _reference_files(root: Path) -> list[Path]:
    refs_dir = root / REFERENCES_DIR
    if not refs_dir.is_dir():
        return []
    files = list(refs_dir.glob("*.md"))

    def sort_key(f: Path) -> tuple[int, str]:
        meta, _ = parse_frontmatter(_read(f) or "")
        order = _meta_map(meta).get("order")
        return (order if isinstance(order, int) else 9999, f.name)

    return sorted(files, key=sort_key)


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
        "version": int(md.get("version") or 0),
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


def load_pack_manifest(pack_id: str) -> dict[str, Any] | None:
    if _is_bundled_pack_id(pack_id):
        kind = "bundled"
    elif _is_plugin_owned_pack_id(pack_id):
        kind = "plugin"
    else:
        kind = "custom"
    for root in _pack_roots(pack_id):
        if (root / PACK_FILE).is_file():
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
            return manifest
    return None


def list_pack_ids() -> list[str]:
    ids: set[str] = set()
    root = appdata_skill_packs_dir()
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and _is_pack_dir(child):
                ids.add(child.name)
    ids.update(_plugin_owned_skill_map().keys())
    return sorted(ids)


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


# --- Selection (deny-list) ---


def normalize_pack_id(pack_id: str) -> str:
    cleaned = pack_id.strip().lower().replace(" ", "_")
    if not _PACK_ID_RE.match(cleaned):
        raise ValueError("Pack id must be lowercase letters, numbers, _ or -")
    return cleaned


def normalize_subskill_id(subskill_id: str) -> str:
    cleaned = subskill_id.strip().lower().replace(" ", "_")
    if not _SUBSKILL_ID_RE.match(cleaned):
        raise ValueError("Subskill id must be lowercase letters, numbers, _ or -")
    return cleaned


def normalize_disabled_packs(pack_ids: list[str] | None) -> list[str]:
    if not pack_ids:
        return []
    available = set(list_pack_ids())
    out: list[str] = []
    seen: set[str] = set()
    for raw in pack_ids:
        try:
            pid = normalize_pack_id(str(raw))
        except ValueError:
            continue
        if pid not in available or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def normalize_enabled_packs(pack_ids: list[str] | None) -> list[str]:
    """Legacy allowlist normalizer — kept for callers that still pass pack lists."""
    if not pack_ids:
        return []
    available = set(list_pack_ids())
    out: list[str] = []
    seen: set[str] = set()
    for raw in pack_ids:
        try:
            pid = normalize_pack_id(raw)
        except ValueError:
            continue
        if pid not in available or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def default_subskills_for_pack(manifest: dict[str, Any]) -> list[str]:
    """Legacy helper — studio/UI may still show default_enabled badges."""
    out: list[str] = []
    for sub in list_root_subskills(manifest):
        sid = str(sub.get("id", ""))
        if not sid:
            continue
        if sub.get("always_on") or sub.get("default_enabled", False):
            out.append(sid)
    return out


def default_skill_selection() -> SkillSelection:
    """All installed packs available (empty deny-list)."""
    return SkillSelection(disabled_packs=[])


def normalize_enabled_subskills(
    enabled_subskills: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    if not isinstance(enabled_subskills, dict):
        return {}
    out: dict[str, list[str]] = {}
    for pack_id, ids in enabled_subskills.items():
        key = str(pack_id or "").strip()
        if not key or not isinstance(ids, list):
            continue
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in ids:
            sid = str(raw or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            cleaned.append(sid)
        out[key] = cleaned
    return out


def merge_selection(
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    *,
    disabled_packs: list[str] | None = None,
) -> SkillSelection:
    """Build a SkillSelection from deny-list and optional per-pack subskill allowlists.

    When enabled_packs is provided (legacy UI), convert to disabled_packs = all − enabled.
    """
    if disabled_packs is None and enabled_packs is not None:
        available = set(list_pack_ids())
        allowed = set(normalize_enabled_packs(enabled_packs))
        disabled_packs = sorted(available - allowed)
    return SkillSelection(
        disabled_packs=normalize_disabled_packs(disabled_packs),
        enabled_subskills=normalize_enabled_subskills(enabled_subskills),
    )


def default_selection_from_settings(settings: Any) -> SkillSelection:
    disabled = list(getattr(settings, "default_disabled_packs", None) or [])
    return SkillSelection(disabled_packs=normalize_disabled_packs(disabled))


def resolve_conversation_selection(conv: Any, settings: Any) -> SkillSelection:
    disabled = getattr(conv, "disabled_packs", None)
    subs = getattr(conv, "enabled_subskills", None)
    if disabled is not None:
        return SkillSelection(
            disabled_packs=normalize_disabled_packs(list(disabled)),
            enabled_subskills=normalize_enabled_subskills(
                subs if isinstance(subs, dict) else None
            ),
        )
    # Legacy chats stored enabled_packs allowlists — treat as no deny (all available).
    base = default_selection_from_settings(settings)
    if isinstance(subs, dict) and subs:
        base.enabled_subskills = normalize_enabled_subskills(subs)
    return base


# --- Prompt assembly (index-only — full bodies via skill_read_subskill) ---


def _short_desc(text: str, limit: int = 140) -> str:
    one = " ".join(str(text or "").strip().split())
    if len(one) <= limit:
        return one
    return one[: limit - 1].rstrip() + "…"


_ALWAYS_ON_PROMPT_CAP = 4000  # chars of injected always_on bodies (excludes index)


def build_skill_prompt(selection: SkillSelection | None = None) -> str:
    """Compact pack + subskill index, plus bodies of non-core always_on refs.

    Full SKILL.md / other refs stay lazy via ``skill_read_subskill``.
    Tags: ``[yours]`` (user-created), ``[store]``, ``[shipped]``, ``[plugin]``.
    """
    sel = selection or default_skill_selection()
    lines: list[str] = [
        "## Available skill packs (lazy-loaded)",
        "Tags: [yours]=you created, [store]=Store, [shipped]=bundled with app, [plugin]=desktop plugin.",
        'Call skill_read_subskill("<pack_id>", "core") to load a pack\'s SKILL.md.',
        'Call skill_read_subskill("<pack_id>") with no id to list its reference files, then load one by id.',
        "Load only what the current task needs — do not fetch every pack.",
        "Always-on rules below are already injected — follow them without re-fetching.",
        "",
    ]
    any_pack = False
    always_bodies: list[str] = []
    always_budget = _ALWAYS_ON_PROMPT_CAP
    for pack_id in sel.enabled_packs:
        manifest = load_pack_manifest(pack_id)
        if not manifest:
            continue
        any_pack = True
        pack_tag = _pack_origin_tag(manifest)
        tag = _index_tag_label(pack_tag)
        label = str(manifest.get("label") or pack_id)
        desc = _short_desc(str(manifest.get("description") or ""))
        if desc:
            lines.append(f"- `{pack_id}` [{tag}] — {label}: {desc}")
        else:
            lines.append(f"- `{pack_id}` [{tag}] — {label}")
        for sub in list_subskills(manifest):
            sid = str(sub.get("id") or "")
            if not sid or not _subskill_allowed(pack_id, sub):
                continue
            sub_tag = _index_tag_label(_subskill_origin_tag(sub, pack_tag))
            if sid == CORE_ID:
                lines.append(f"  - `{sid}` [{sub_tag}]")
                continue
            sub_desc = _short_desc(str(sub.get("description") or sub.get("label") or ""))
            if sub_desc:
                lines.append(f"  - `{sid}` [{sub_tag}] — {sub_desc}")
            else:
                lines.append(f"  - `{sid}` [{sub_tag}]")
            # Inject non-core always_on bodies (capped) so agents get hard rules.
            if bool(sub.get("always_on")) and always_budget > 0:
                body = (read_subskill_body(pack_id, sid) or "").strip()
                if body:
                    chunk = f"### {pack_id}/{sid}\n\n{body}"
                    if len(chunk) > always_budget:
                        chunk = chunk[: always_budget - 1].rstrip() + "…"
                    always_bodies.append(chunk)
                    always_budget -= len(chunk)
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
        'Call skill_read_subskill("<pack_id>", "core") for SKILL.md; omit id to list refs.',
        "Load only what the current task needs.",
        "",
    ]
    any_pack = False
    for pack_id in sel.enabled_packs:
        manifest = load_pack_manifest(pack_id)
        if not manifest:
            continue
        any_pack = True
        pack_tag = _pack_origin_tag(manifest)
        tag = _index_tag_label(pack_tag)
        label = str(manifest.get("label") or pack_id)
        lines.append(f"- `{pack_id}` [{tag}] — {label}")
        for sub in list_subskills(manifest):
            sid = str(sub.get("id") or "")
            if not sid or not _subskill_allowed(pack_id, sub):
                continue
            lines.append(f"  - `{sid}`")
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


def skill_read_subskill(pack_id: str, subskill_id: str = "") -> str:
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
    return f"{heading}\n\n{body.strip()}"


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
            "version": int(manifest.get("version") or 0),
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


# --- Seeding ---


def _copy_pack_tree(src: Path, dest: Path) -> None:
    """Full replace (reset / first install). Prefer :func:`_merge_pack_tree` for updates."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _merge_pack_tree(src: Path, dest: Path, *, ref_origin: str = ORIGIN_SHIPPED) -> None:
    """Update SKILL.md + shipped refs; never delete or overwrite ``origin: user`` refs."""
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
    for md in sorted(refs_src.glob("*.md")):
        out = refs_dest / md.name
        if out.is_file() and _is_user_origin_file(out):
            continue
        text = _stamp_ref_origin(md.read_text(encoding="utf-8"), ref_origin)
        out.write_text(text, encoding="utf-8")


def _pack_version(root: Path) -> int:
    raw = _read(root / PACK_FILE)
    if raw is not None:
        meta, _ = parse_frontmatter(raw)
        return int(_meta_map(meta).get("version") or 0)
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
    try:
        (dest / LICENSE_FILE).write_text(
            _license_text_for_pack({**manifest, "id": pid}), encoding="utf-8"
        )
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
        if dest_pack.is_dir() and dest_ver > bundled_ver:
            logs.append(
                f"Skill pack {pack_id} v{dest_ver} kept (newer than bundled v{bundled_ver})"
            )
            _write_pack_license_file(pack_id)
            continue
        if dest_pack.is_dir() and not force and dest_ver >= bundled_ver:
            logs.append(f"Skill pack {pack_id} v{dest_ver} current")
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
    md["version"] = int(md.get("version") or 0) + 1
    path.write_text(serialize_frontmatter(meta, body), encoding="utf-8")
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
        "version": int(manifest.get("version") or 0),
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


# --- Export / import ---


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
        "version": int(manifest.get("version") or 0),
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


def import_skill_pack_from_zip(
    src_path: Path,
    *,
    pack_id: str | None = None,
    replace: bool = False,
    source: str | None = None,
    store_slug: str | None = None,
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
    if owner:
        return {
            "ok": False,
            "conflict": True,
            "existing_id": target_id,
            "error": f"Skill {target_id!r} is owned by plugin {owner!r} — uninstall that plugin first",
        }
    src_kind = (source or "").strip().lower() or SOURCE_IMPORT
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
    return result


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
    return build_skill_prompt(
        merge_selection(
            enabled_packs=enabled_packs,
            enabled_subskills=enabled_subskills,
            disabled_packs=disabled_packs,
        )
    )


def normalize_enabled_skills(filenames: list[str] | None) -> list[str]:
    """Legacy: treat filenames as pack ids."""
    return normalize_enabled_packs(filenames)
