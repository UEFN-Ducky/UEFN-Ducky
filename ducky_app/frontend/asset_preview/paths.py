"""Map disk paths under Content to Unreal asset paths."""

from __future__ import annotations

from pathlib import Path

_PREVIEWABLE_SUFFIXES = frozenset({".uasset", ".umap"})


def is_previewable_binary(relative_path: str) -> bool:
    return Path((relative_path or "").strip().replace("\\", "/")).suffix.lower() in _PREVIEWABLE_SUFFIXES


def disk_path_to_asset_path(relative: str, content_root: str = "/Game/") -> str:
    """``Content/Textures/T_Foo.uasset`` → ``/Game/Textures/T_Foo``."""
    rel = (relative or "").strip().replace("\\", "/")
    if rel.lower().startswith("content/"):
        rel = rel[len("content/") :]
    stem = rel.rsplit(".", 1)[0] if "." in rel else rel
    root = (content_root or "/Game/").rstrip("/")
    if not root.startswith("/"):
        root = f"/{root}"
    return f"{root}/{stem}"
