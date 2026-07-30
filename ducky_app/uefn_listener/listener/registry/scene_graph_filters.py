"""Pure helpers for Scene Graph entity listing filters (no unreal import)."""

from __future__ import annotations


def is_junk_entity_name(name: str) -> bool:
    return (
        name.startswith("Default__")
        or name.startswith("TRASH_")
        or name.startswith("REINST_")
    )


def is_proxy_shadow_path(path: str) -> bool:
    return ".EntityProxyActor_" in path or ".EntityProxyActor." in path


def spatial_to_unreal_xyz(translation: list[float]) -> tuple[float, float, float]:
    if len(translation) != 3:
        raise ValueError("translation must be [forward, left, up]")
    forward, left, up = float(translation[0]), float(translation[1]), float(translation[2])
    return forward, -left, up
