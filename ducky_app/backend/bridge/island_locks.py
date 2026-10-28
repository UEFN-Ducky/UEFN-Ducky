"""An agent never changes what the other PC is editing (Unreal Revision Control locks).

Two PCs share one island through Lore. UEFN locks an actor's file (or an asset's) on the
server as soon as it is modified, until the check-in. ``send_command`` asks :func:`refusal`
before it posts a mutating command: when a target of that command is locked by another
owner, the command is refused with a sentence the agent can act on, and nothing is sent.

Cost: a read, a creation (a new actor or asset has no lock yet), or a command whose params
name no existing actor/asset costs a dict lookup. Otherwise ``island_watch.others_locks``
(kept by the app's watch thread); only when someone else holds locks does one
``revision_targets`` read ask the listener which file each target lives in.
Anything unknown (no island, no lock info, an older listener) lets the command through.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

#: Params that name the actor/asset/folder a mutating command changes. Others only read
#: (``target_path`` of a wire, ``texture_path``, ``parent_path``...) and never block.
TARGET_KEYS: tuple[str, ...] = (
    "actor_path", "actor_paths", "child_path", "manager_path",
    "asset_path", "old_path", "material_path", "material_instance_path", "system_path",
    "widget_path", "data_table_path", "directory",
)

#: Commands whose generic keys would name something they only read.
_KEYS_BY_COMMAND: dict[str, tuple[str, ...]] = {
    "assign_material_to_mesh": ("actor_path",),  # the material is only referenced
    "add_entity_component": (),  # asset_path is the component's asset, not changed
    "export_asset": (),  # writes outside the project
    "control_niagara_actor": (),  # playback, not a project change
    "landscape_rename": ("actor_path", "label"),
    "landscape_sculpt": ("actor_path", "label"),
}


def targets(command: str, params: Mapping[str, Any] | None) -> list[str]:
    """Actor paths/labels and asset/folder paths this command would change, in order."""
    params = params or {}
    out: list[str] = []
    for key in _KEYS_BY_COMMAND.get(command, TARGET_KEYS):
        value = params.get(key)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item.strip() and item.strip() not in out:
                out.append(item.strip())
    return out


def _guarded(command: str) -> bool:
    from backend.workspace.editor_ops import MUT_READ, classify

    spec = classify(command)
    return spec.mutates != MUT_READ and not spec.creates


def _watch():
    from frontend.ui_web.verse_editor.urc import island_watch

    return island_watch


def message(label: str, owner: str, more: int = 0) -> str:
    text = f"{label} is being edited on the other PC by {owner or 'someone else'}: pick something else or wait"
    if more:
        text += f" ({more} more of its targets {'is' if more == 1 else 'are'} locked too)"
    return text


def refusal(
    command: str,
    params: Mapping[str, Any] | None,
    island_dir: Callable[[], str],
    resolve: Callable[[list[str]], Mapping[str, Any]],
) -> str:
    """Why this command must not run now, or "" (also whenever anything is unknown).

    ``island_dir()`` names the island UEFN has open; ``resolve(idents)`` returns
    ``{ident: {"file": abs path of its .uasset/.umap or folder, "label": str}}``.
    """
    try:
        if not _guarded(command):
            return ""
        idents = targets(command, params)
        if not idents:
            return ""
        island = island_dir()
        if not island:
            return ""
        watch = _watch()
        locks = watch.others_locks(island) or {}
        if not locks:
            return ""
        resolved = resolve(idents) or {}
        hits: list[tuple[str, str]] = []
        for ident in idents:
            info = resolved.get(ident)
            if not isinstance(info, Mapping) or not info.get("file"):
                continue
            owner = _owner(watch.norm_path(str(info["file"])), locks)
            if owner is not None:
                hits.append((str(info.get("label") or ident), owner))
        if not hits:
            return ""
        return message(hits[0][0], hits[0][1], len(hits) - 1)
    except Exception:  # noqa: BLE001 - lock info is a courtesy: never fail a command over it
        return ""


def _owner(path: str, locks: Mapping[str, str]) -> str | None:
    """Who else holds ``path`` (a file, or any file under a folder), or None."""
    if path in locks:
        return str(locks[path] or "")
    prefix = path.rstrip("/") + "/"
    for locked, owner in locks.items():
        if locked.startswith(prefix):
            return str(owner or "")
    return None


def locked_files(island: str, paths: Iterable[str]) -> list[dict[str, str]]:
    """``[{file, owner}]`` for each of ``paths`` another PC holds. [] when unknown."""
    try:
        watch = _watch()
        locks = watch.others_locks(island) or {}
        if not locks:
            return []
        out = []
        for path in paths:
            owner = locks.get(watch.norm_path(path))
            if owner is not None:
                out.append({"file": path, "owner": str(owner or "")})
        return out
    except Exception:  # noqa: BLE001
        return []
