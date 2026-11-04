"""foliage_get_stats: the worldgen foliage/HISM actors a page at a time (split out of worldgen.py)."""

from __future__ import annotations

from typing import Optional

import unreal

from listener.dispatch import register
from listener.registry.worldgen import _DEFAULT_FOLDER, _find_tagged_actors

_FOLIAGE_MARKERS = ("HISM", "Foliage", "InstancedFoliage", "WG_")


def _foliage_label(a) -> str:
    try:
        return a.get_actor_label() or ""
    except Exception:
        return ""


def foliage_get_stats(folder_prefix: str = "", offset: int = 0, limit: Optional[int] = None) -> dict:
    """Summarize tagged worldgen foliage/HISM actors, a page at a time (instance counts are
    read for the page's actors only)."""
    from listener import paging

    folder_prefix = folder_prefix or _DEFAULT_FOLDER
    paged = paging.page(
        _find_tagged_actors(folder_prefix=folder_prefix),
        _foliage_entry,
        offset=offset,
        limit=limit,
        keep=lambda a: any(k in _foliage_label(a) for k in _FOLIAGE_MARKERS),
    )
    return paging.reply("actors", paged, folder_prefix=folder_prefix)


def _foliage_entry(a) -> dict:
    label = _foliage_label(a)
    entry = {"actor_path": a.get_path_name(), "label": label, "class": a.get_class().get_name()}
    try:
        hisms = a.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        entry["hism_components"] = len(hisms or [])
        total = 0
        for c in hisms or []:
            try:
                total += int(c.get_instance_count())
            except Exception:
                pass
        entry["instance_count"] = total if total > 0 else (1 if "Foliage" in label or "HISM" in label else 0)
    except Exception:
        entry["instance_count"] = 1 if "Foliage" in label else 0
    return entry


register("foliage_get_stats")(foliage_get_stats)
