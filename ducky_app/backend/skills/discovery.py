"""Where skill packs live: AppData, plugin-owned folders and the bundled packs, plus pack ids."""

from __future__ import annotations

import sys
from pathlib import Path

from backend.skills.frontmatter import _meta_map, _read, parse_frontmatter
from backend.skills.layout import (
    _PACK_ID_RE,
    _SUBSKILL_ID_RE,
    PACK_FILE,
    REFERENCES_DIR,
    SKILL_PACKS_DIR,
)
from backend.skills.ownership import _plugin_owned_skill_map
from backend.skills.passes import _PASS_MEMO


def appdata_dir() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir()


def appdata_skill_packs_dir() -> Path:
    return appdata_dir() / SKILL_PACKS_DIR


def bundled_skill_packs_dir() -> Path | None:
    memo = _PASS_MEMO.get()
    if memo is not None:
        if "bundled" not in memo:
            memo["bundled"] = _bundled_skill_packs_dir()
        return memo["bundled"]
    return _bundled_skill_packs_dir()


def _bundled_skill_packs_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "frontend" / SKILL_PACKS_DIR
        if p.is_dir():
            return p
    # backend/skills/<module>.py -> ducky_app/frontend/skill_packs (parents[2] is ducky_app;
    # parent.parent was backend/, so dev runs never found the bundled packs and only a
    # previously seeded AppData made the skills tests pass).
    repo = Path(__file__).resolve().parents[2] / "frontend" / SKILL_PACKS_DIR
    if repo.is_dir():
        return repo
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


def list_pack_ids() -> list[str]:
    ids: set[str] = set()
    root = appdata_skill_packs_dir()
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and _is_pack_dir(child):
                ids.add(child.name)
    ids.update(_plugin_owned_skill_map().keys())
    return sorted(ids)


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
