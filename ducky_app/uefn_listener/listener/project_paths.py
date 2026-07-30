"""Pin Content Browser *create* paths to the active project's content_root.

UEFN islands must not invent ``/Game/Materials`` (or ``/Game/VFX``, ``/Game/UI``, …)
for new assets — that yields unsaved ``/Game/...`` packages and cook/publish
``Disallowed reference to /Game/...``. Reads under ``/Game/Creative`` stay OK;
this module is only for create/save destinations.
"""

from __future__ import annotations

import os

import unreal


def content_root() -> str:
    """Active project Content Browser mount, e.g. ``/VideoTest/`` (trailing slash)."""
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        world = None
    if world:
        parts = world.get_path_name().split("/")
        if len(parts) >= 2 and parts[1]:
            name = parts[1]
            if name not in ("Temp", "Engine", "Script", "Game"):
                return f"/{name}/"
    try:
        proj = str(unreal.Paths.project_dir() or "").rstrip("/\\")
        if proj:
            name = os.path.basename(proj)
            if name:
                return f"/{name}/"
    except Exception:
        pass
    return ""


def pin_folder_string(folder: str, root: str, *, default_leaf: str = "Materials") -> str:
    """Pure path pin (no UE). ``root`` is ``/VideoTest`` or ``/VideoTest/``."""
    root = (root or "").rstrip("/")
    if not root:
        raise RuntimeError(
            "No active UEFN project content_root — open an island project before "
            "creating materials/meshes/VFX/UI assets."
        )

    f = (folder or "").strip().replace("\\", "/")
    if not f:
        return f"{root}/{default_leaf}".rstrip("/") if default_leaf else root

    while f.endswith("/") and len(f) > 1:
        f = f[:-1]

    if f == root or f.startswith(root + "/"):
        return f

    low = f.lower()
    if low == "/engine" or low.startswith("/engine/"):
        raise ValueError(f"Cannot create assets under {f!r} — use {root}/{default_leaf}/...")
    if low == "/fortnite" or low.startswith("/fortnite/"):
        raise ValueError(f"Cannot create assets under {f!r} — Fortnite gallery is read-only")

    if low == "/game":
        return f"{root}/{default_leaf}".rstrip("/") if default_leaf else root
    if low.startswith("/game/"):
        rest = f[6:]  # len("/Game/") == 6
        if not rest:
            rest = default_leaf
        return f"{root}/{rest}".rstrip("/") if rest else root

    if not f.startswith("/"):
        return f"{root}/{f}"

    leaf_hint = default_leaf or "Materials"
    raise ValueError(
        f"Create folder {f!r} is outside project mount {root}/. "
        f"Pass {root}/{leaf_hint}/... — never invent /Game/... for new island assets "
        "(causes Disallowed reference / unsaved /Game packages)."
    )


def pin_project_folder(folder: str = "", *, default_leaf: str = "Materials") -> str:
    """Return a create folder under ``content_root``, retargeting ``/Game/...``."""
    return pin_folder_string(folder, content_root(), default_leaf=default_leaf)


def pin_asset_path_string(asset_path: str, root: str, *, default_leaf: str = "Assets") -> str:
    """Pin a full asset path (``/Game/Materials/M_X`` → ``/VideoTest/Materials/M_X``)."""
    p = (asset_path or "").strip().replace("\\", "/")
    if not p:
        raise ValueError("asset path required")
    # Drop object suffix: /Pkg/Name.Name → /Pkg/Name
    package = p.split(".")[0]
    while package.endswith("/") and len(package) > 1:
        package = package[:-1]
    if "/" not in package.lstrip("/"):
        # Bare name → under default_leaf
        folder = pin_folder_string("", root, default_leaf=default_leaf)
        return f"{folder}/{package.lstrip('/')}"
    folder, name = package.rsplit("/", 1)
    pinned = pin_folder_string(folder, root, default_leaf=default_leaf)
    return f"{pinned}/{name}"


def pin_project_asset_path(asset_path: str, *, default_leaf: str = "Assets") -> str:
    """Pin a create/duplicate/rename destination asset path to content_root."""
    return pin_asset_path_string(asset_path, content_root(), default_leaf=default_leaf)
