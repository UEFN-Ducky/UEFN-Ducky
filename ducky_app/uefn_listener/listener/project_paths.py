"""Pin Content Browser *create* paths to the active project's content_root.

UEFN islands must not invent ``/Game/Materials`` (or ``/Game/VFX``, ``/Game/UI``, …)
for new assets — that yields unsaved ``/Game/...`` packages and cook/publish
``Disallowed reference to /Game/...``. Reads under ``/Game/Creative`` stay OK;
this module is only for create/save destinations.
"""

from __future__ import annotations

import os

import unreal

# Epic Content Browser roots. Mirroring these under the island
# (/MyProject/Creative/...) is not a project folder.
_EPIC_GALLERY = frozenset({
    "creative",
    "environments",
    "packages",
    "athena",
    "items",
    "building",
    "props",
})

_MESH_EXT = frozenset({".fbx", ".obj", ".gltf", ".glb", ".usd"})
_TEXTURE_EXT = frozenset({".png", ".jpg", ".jpeg", ".tga", ".exr"})
_AUDIO_EXT = frozenset({".wav", ".ogg", ".mp3"})


def content_root() -> str:
    """Active project Content Browser mount, e.g. ``/MyProject/`` (trailing slash)."""
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
    """Pure path pin (no UE). ``root`` is ``/MyProject`` or ``/MyProject/``."""
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
        rest = f[len(root):].lstrip("/")
        collapsed = _collapsed_epic_folder(rest, root, default_leaf) if rest else ""
        return collapsed or f

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
        collapsed = _collapsed_epic_folder(rest, root, default_leaf)
        if collapsed:
            return collapsed
        return f"{root}/{rest}".rstrip("/") if rest else root

    if not f.startswith("/"):
        return f"{root}/{f}"

    leaf_hint = default_leaf or "Materials"
    raise ValueError(
        f"Create folder {f!r} is outside project mount {root}/. "
        f"Pass {root}/{leaf_hint}/... — never invent /Game/... for new island assets "
        "(causes Disallowed reference / unsaved /Game packages)."
    )


def _collapsed_epic_folder(rest: str, root: str, default_leaf: str) -> str:
    """``Creative/BuildingActors/.../SM_X`` → ``{root}/{leaf}/SM_X``. Empty if not a gallery path."""
    first = rest.split("/", 1)[0].lower()
    if first not in _EPIC_GALLERY:
        return ""
    leaf = (default_leaf or "Imported").strip("/") or "Imported"
    last = rest.rstrip("/").rsplit("/", 1)[-1]
    if not last or last.lower() in _EPIC_GALLERY:
        return f"{root}/{leaf}"
    return f"{root}/{leaf}/{last}"


def import_destination_leaf(source_file: str) -> str:
    """Content folder for an import, from the file extension."""
    ext = os.path.splitext(source_file or "")[1].lower()
    if ext in _MESH_EXT:
        return "Meshes"
    if ext in _TEXTURE_EXT:
        return "Textures"
    if ext in _AUDIO_EXT:
        return "Audio"
    return "Imported"


def require_island_ref(path: str, root: str | None = None) -> str:
    """Allow the project mount and ``/Game/Creative``. Refuse other ``/Game`` refs.

    ponytail: prefix allowlist, not a validator run. Upgrade path is calling
    ``validate_uefn_asset`` after assigning.
    """
    p = (path or "").strip().replace("\\", "/")
    if not p:
        raise ValueError("asset path required")
    low = p.lower()
    if low == "/game/creative" or low.startswith("/game/creative/"):
        return p
    if low == "/game" or low.startswith("/game/"):
        raise ValueError(
            f"{p} fails publish (AssetReferenceRestrictions). "
            "Use a /Game/Creative material or a project material with project textures."
        )
    mount = content_root() if root is None else root
    mount = (mount or "").rstrip("/")
    if mount and (low == mount.lower() or low.startswith(mount.lower() + "/")):
        return p
    raise ValueError(
        f"{p} fails publish (AssetReferenceRestrictions). "
        "Use a /Game/Creative material or a project material with project textures."
    )


def pin_project_folder(folder: str = "", *, default_leaf: str = "Materials") -> str:
    """Return a create folder under ``content_root``, retargeting ``/Game/...``."""
    return pin_folder_string(folder, content_root(), default_leaf=default_leaf)


def pin_asset_path_string(asset_path: str, root: str, *, default_leaf: str = "Assets") -> str:
    """Pin a full asset path (``/Game/Materials/M_X`` → ``/MyProject/Materials/M_X``)."""
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
