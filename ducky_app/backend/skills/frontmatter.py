"""SKILL.md / reference frontmatter: read, parse, write, and the provenance it carries.

Pack metadata lives in SKILL.md frontmatter (``metadata.label`` /
``metadata.version``). Reference metadata (label, default_enabled,
load_condition, order) lives in each reference file's own frontmatter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.skills.layout import (
    _PLAIN_SCALAR_RE,
    CORE_ID,
    DEFAULT_COMMERCIAL_LICENSE,
    ORIGIN_PLUGIN,
    ORIGIN_SHIPPED,
    ORIGIN_STORE,
    ORIGIN_USER,
    SOURCE_STORE,
)


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
