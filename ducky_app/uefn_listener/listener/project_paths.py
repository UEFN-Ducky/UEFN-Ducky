"""UEFN project content mount helpers.

Island projects mount Content as ``/<ProjectName>/`` (e.g. ``/catland/``), NOT
``/Game/``. Creating assets under ``/Game/...`` in those projects yields unsaved
packages and publish failures ("Disallowed reference to /Game/...").

Always resolve folders through :func:`normalize_project_folder` before create.
"""

from __future__ import annotations

from typing import Optional

# Engine / Creative catalog mounts agents may still *read* — never rewrite these.
_ENGINE_PREFIXES = (
    "/Engine/",
    "/Fortnite/",
    "/Script/",
    "/Creative/",
    "/Game/Creative/",
    "/Game/Athena/",
    "/Game/Environments/",
    "/Game/BuildingActors/",
    "/Game/Items/",
)


def content_root() -> str:
    """Return the open project's content mount, e.g. ``/catland`` (no trailing slash).

    Falls back to ``/Game`` only when the editor world path cannot be resolved.
    """
    try:
        import unreal

        world = unreal.EditorLevelLibrary.get_editor_world()
        if world:
            parts = world.get_path_name().split("/")
            if len(parts) >= 2 and parts[1].strip():
                name = parts[1].strip()
                if name and name.lower() not in ("engine", "script", "temp", "transient"):
                    return f"/{name}"
    except Exception:
        pass
    return "/Game"


def project_subfolder(*parts: str) -> str:
    """``project_subfolder("Materials")`` → ``/catland/Materials``."""
    root = content_root().rstrip("/")
    segs = [p.strip("/\\") for p in parts if p and str(p).strip("/\\")]
    if not segs:
        return root
    return root + "/" + "/".join(segs)


def _is_engine_or_catalog(path: str) -> bool:
    p = path if path.startswith("/") else f"/{path}"
    return any(p.startswith(pref) for pref in _ENGINE_PREFIXES)


def normalize_project_folder(folder: Optional[str], *, default_subpath: str = "Materials") -> str:
    """Resolve a create-folder path onto the project content mount.

    - Empty / missing → ``/<project>/<default_subpath>``
    - ``/Game/...`` when project mount is not ``/Game`` → rewrite to ``/<project>/...``
    - Already under the project mount → unchanged
    - Engine / Creative catalog paths → unchanged (read-only catalog use)
    """
    root = content_root().rstrip("/")
    raw = (folder or "").strip().replace("\\", "/")
    if not raw:
        return project_subfolder(default_subpath)

    if not raw.startswith("/"):
        raw = f"/{raw}"

    # Explicit project-relative without mount: Materials/Foo → /catland/Materials/Foo
    if not raw.startswith(root + "/") and raw.count("/") == 1 and raw.strip("/") and not raw.startswith("/Game"):
        # "/Materials" style
        return f"{root}{raw}" if raw.startswith("/") else f"{root}/{raw}"

    if raw == "/Game" or raw.startswith("/Game/"):
        if root == "/Game":
            return raw.rstrip("/") or "/Game"
        if _is_engine_or_catalog(raw):
            return raw.rstrip("/")
        # /Game/Materials → /catland/Materials ; /Game → /catland
        suffix = raw[len("/Game") :].lstrip("/")
        return f"{root}/{suffix}" if suffix else root

    # Bare "Materials" already handled; paths under wrong absolute roots stay as given
    # only if they already start with this project's root.
    if raw.startswith(root + "/") or raw == root:
        return raw.rstrip("/") or root

    # Relative-looking multi-segment without leading project: BlockoutCity/Materials
    if not raw.startswith("/Engine") and not raw.startswith("/Fortnite") and not raw.startswith("/Script"):
        # If it looks like a project asset path missing the mount, pin it.
        # e.g. agents sometimes pass "BlockoutCity/Materials"
        if not raw.startswith("/"):
            return f"{root}/{raw.strip('/')}"
    return raw.rstrip("/") or root


def normalize_project_asset_path(asset_path: Optional[str]) -> str:
    """Rewrite ``/Game/...`` asset paths onto the project mount when appropriate."""
    raw = (asset_path or "").strip().replace("\\", "/")
    if not raw:
        return raw
    if not raw.startswith("/"):
        raw = f"/{raw}"
    root = content_root().rstrip("/")
    if root == "/Game":
        return raw
    if raw == "/Game" or raw.startswith("/Game/"):
        if _is_engine_or_catalog(raw):
            return raw
        suffix = raw[len("/Game") :].lstrip("/")
        return f"{root}/{suffix}" if suffix else root
    return raw
