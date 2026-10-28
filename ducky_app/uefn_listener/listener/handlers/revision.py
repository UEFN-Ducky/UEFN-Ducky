"""Unreal Revision Control (Lore): push saved island changes, pull the remote.

The host works out *which* files are not on the remote yet (``lore_cli`` runs in the
Ducky process: no subprocess on the editor thread). These commands only do what has
to happen inside UEFN: save, check in, sync, reload the map.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

import unreal

from listener.dispatch import register
from listener.project_paths import content_root, island_directory


def _dirty_packages() -> list:
    utils = unreal.EditorLoadingAndSavingUtils
    return list(utils.get_dirty_content_packages() or []) + list(utils.get_dirty_map_packages() or [])


def _names(packages) -> List[str]:
    return [str(p.get_name()) for p in packages]


def _last_error() -> str:
    try:
        return str(unreal.SourceControl.last_error_msg() or "")
    except Exception:
        return ""


@register("revision_status")
def cmd_revision_status() -> dict:
    """The open island's folder, its unsaved packages and the revision control provider. Never touches the network."""
    sc = unreal.SourceControl
    return {
        "island": island_directory() or "",
        "dirty": _names(_dirty_packages()),
        "provider": str(sc.current_provider() or ""),
        "available": bool(sc.is_available()),
    }


@register("revision_check_in")
def cmd_revision_check_in(files: Optional[List[str]] = None, description: str = "") -> dict:
    """Save every dirty package, then check ``files`` plus the island's saved packages in.

    Pushes to the remote with no dialog. ``files`` come from the host (lore_cli).
    """
    if not str(description or "").strip():
        raise ValueError("description must not be empty (it is the check-in message)")
    packages = _dirty_packages()
    saved = _names(packages)
    if packages and not unreal.EditorLoadingAndSavingUtils.save_packages(packages, True):
        return {"ok": False, "checked_in": [], "saved": [], "error": "saving the dirty packages failed"}
    # Only the island's own packages can be checked in (not /Temp, not Fortnite's).
    mount = content_root()
    own = [n for n in saved if not mount or n.startswith(mount)]
    targets = list(dict.fromkeys([*(files or []), *own]))
    if not targets:
        return {"ok": True, "checked_in": [], "saved": saved, "error": ""}
    ok = bool(unreal.SourceControl.check_in_files(targets, str(description), True))
    return {
        "ok": ok,
        "targets": targets,
        "checked_in": targets if ok else [],
        "saved": saved,
        "error": "" if ok else _last_error() or "check-in failed",
    }


@register("revision_sync")
def cmd_revision_sync(force: bool = False, reload: bool = True) -> dict:
    """Pull the remote into the island, then reload the open map to show the changes.

    A loaded actor keeps its file open, so Lore can't overwrite it: the map is swapped
    for a blank one during the sync, then opened again. Refuses (does nothing) while
    packages are unsaved, unless ``force``; even then it never unloads or reloads over
    unsaved work (only files that aren't loaded arrive then).
    """
    dirty = _names(_dirty_packages())
    if dirty and not force:
        return {"ok": False, "needs_save": dirty, "synced": False, "reloaded": False, "error": ""}
    island = island_directory()
    content = os.path.join(island, "Content") if island else ""
    if not content or not os.path.isdir(content):
        return {"ok": False, "synced": False, "reloaded": False, "error": f"island Content folder not found: {island!r}"}
    import glob

    # The island's descriptors sit beside Content/ and change too (.uefnproject lists plugins).
    targets = [content.replace("\\", "/") + "/"]
    for pattern in ("*.uefnproject", "*.uplugin"):
        targets += [p.replace("\\", "/") for p in sorted(glob.glob(os.path.join(island, pattern)))]
    started = time.time()
    world = unreal.EditorLevelLibrary.get_editor_world()
    map_path = str(world.get_path_name()).split(".", 1)[0] if world else ""
    release = bool(reload and not dirty and map_path)
    if release and not unreal.EditorLoadingAndSavingUtils.new_blank_map(False):
        return {"ok": False, "synced": False, "reloaded": False, "seconds": round(time.time() - started, 2),
                "error": "could not close the map before syncing"}
    synced = bool(unreal.SourceControl.sync_files(targets, True))
    error = "" if synced else _last_error() or "sync failed"
    # Open the map again even after a failed sync: the person must never be left on the blank one.
    reloaded = bool(unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(map_path)) if release else False
    return {
        "ok": synced,
        "synced": synced,
        "reloaded": reloaded,
        "dirty": dirty,
        "seconds": round(time.time() - started, 2),
        "error": error,
    }


@register("revision_conflicts")
def cmd_revision_conflicts(paths: Optional[List[str]] = None) -> dict:
    """Which of ``paths`` (island files) revision control marks conflicted, after a pull.

    ``query_file_state(s)`` asks the provider again (the editor's state cache can be stale).
    Never opens a dialog; never resolves anything.
    """
    files = list(dict.fromkeys(str(p).strip() for p in paths or [] if str(p or "").strip()))
    if not files:
        return {"conflicted": [], "error": ""}
    sc = unreal.SourceControl
    try:
        if hasattr(sc, "query_file_states"):
            states = list(sc.query_file_states(files, True) or [])
        else:
            states = [sc.query_file_state(f, True) for f in files]
    except Exception as exc:
        return {"conflicted": [], "error": _last_error() or f"querying the file states failed: {exc}"}
    if len(states) != len(files):
        return {"conflicted": [], "error": f"{len(states)} states for {len(files)} files"}
    conflicted = []
    for path, state in zip(files, states):
        try:
            if bool(state.get_editor_property("is_conflicted")):
                conflicted.append(path)
        except Exception:
            continue
    return {"conflicted": conflicted, "error": ""}


def _package_file(package: str, mount: str, content: str) -> str:
    """``/<mount>/A/B`` (object or subobject suffix allowed) → its ``.uasset``/``.umap`` under
    ``Content``, or the folder when it names one. Empty for another mount or nothing on disk."""
    name = package.split(":", 1)[0].split(".", 1)[0].rstrip("/")
    if not mount or not name.lower().startswith(mount.lower()):
        return ""
    base = os.path.join(content, *name[len(mount):].split("/"))
    for candidate in (base + ".uasset", base + ".umap", base):
        if os.path.exists(candidate):
            return os.path.abspath(candidate).replace("\\", "/")
    return ""


def _actor_entry(actor, mount: str, content: str) -> Optional[dict]:
    try:
        # A World Partition actor is its own package (__ExternalActors__); any other one
        # lives in the map's package, so the map is what a lock would hold.
        path = _package_file(str(actor.get_package().get_name()), mount, content)
        label = str(actor.get_actor_label() or "")
    except Exception:
        return None
    return {"file": path, "label": label} if path else None


@register("revision_targets")
def cmd_revision_targets(paths: Optional[List[str]] = None) -> dict:
    """Which island file each actor (path or label), asset or folder path lives in.

    For the host's lock guard: ``{targets: {ident: {file, label}}}``, unknown idents left
    out. One lookup index (the tick's) and a few stats; never walks the disk.
    """
    island = island_directory()
    mount = content_root()
    content = os.path.join(island, "Content") if island else ""
    out: dict = {}
    if not content or not mount:
        return {"targets": out}
    from listener import lookup

    for ident in paths or []:
        ident = str(ident or "").strip()
        if not ident or ident in out:
            continue
        actor = lookup.find_actor(ident)
        entry = _actor_entry(actor, mount, content) if actor is not None else None
        # A missing actor's path (Map.Map:PersistentLevel.X) would name the map: leave it out.
        if entry is None and ident.startswith("/") and ":" not in ident:
            path = _package_file(ident, mount, content)
            if path:
                entry = {"file": path, "label": ident.split(".", 1)[0].rstrip("/").rsplit("/", 1)[-1]}
        if entry is not None:
            out[ident] = entry
    return {"targets": out}
