"""Per-tick actor lookup index.

Many handlers resolve an actor by path name or label. Doing
``get_all_level_actors()`` plus a linear scan inside every handler — and again
on repeated lookups in one tick — is O(n) per lookup and dominates
large-level latency.

This module caches the level actor list and path/label dictionaries for the
duration of a single editor tick. ``tick_handler`` calls :func:`invalidate` at
the top of each tick so the index never serves a stale view across frames (actors
spawned/deleted within one tick batch invalidate as needed via :func:`invalidate`).
"""

from __future__ import annotations

from typing import List, Optional

import unreal

from listener.serialize import is_live

_actors: Optional[list] = None
_by_path: Optional[dict] = None
_by_label: Optional[dict] = None


def invalidate() -> None:
    """Drop the cached index. Called once per tick and after mutations."""
    global _actors, _by_path, _by_label
    _actors = None
    _by_path = None
    _by_label = None


def actor_list() -> list:
    """Return the level actors, cached for this tick."""
    global _actors
    if _actors is None:
        actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        # Filter invalid/pending-kill actors once, at the source: every consumer
        # (get_all_actors, find_actor, serialize, level_design …) then dereferences
        # only live refs, so a stale entry can't trigger an uncatchable native crash.
        _actors = [a for a in actor_sub.get_all_level_actors() if is_live(a)]
    return _actors


def _ensure_maps() -> None:
    global _by_path, _by_label
    if _by_path is not None and _by_label is not None:
        return
    _by_path = {}
    _by_label = {}
    for a in actor_list():
        try:
            _by_path[a.get_path_name()] = a
            # First label wins, matching the original linear-scan order.
            _by_label.setdefault(a.get_actor_label(), a)
        except Exception:
            continue


def find_actor(path_or_label: str) -> Optional[unreal.Actor]:
    """Return the actor matching ``path_or_label`` (path takes priority), or None."""
    _ensure_maps()
    actor = _by_path.get(path_or_label)
    if actor is not None:
        return actor
    return _by_label.get(path_or_label)


def require_actor(path_or_label: str) -> unreal.Actor:
    """Like :func:`find_actor` but raises ``ValueError`` if not found."""
    actor = find_actor(path_or_label)
    if actor is None:
        raise ValueError(f"Actor not found: {path_or_label}")
    return actor


def find_actors(paths_or_labels: List[str]) -> List[unreal.Actor]:
    """Resolve many identifiers in order, skipping any that don't match."""
    out: List[unreal.Actor] = []
    for ident in paths_or_labels:
        actor = find_actor(ident)
        if actor is not None:
            out.append(actor)
    return out
