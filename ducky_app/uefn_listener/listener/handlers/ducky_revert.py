"""Remove one thing an agent created, when the change record says it did.

Agents also have ``delete_asset`` / ``delete_actors`` (same mechanical asset
gate as here). This command stays unexposed: the journal is the provenance
authority (only a user-initiated revert of a recorded creation), and the
listener re-checks independently.

Neither side alone is trusted. This command is not exposed as an agent tool
(``_NEVER_EXPOSE``), is refused by name through ``listener_command``, and is
blocked in plan mode.
"""

from __future__ import annotations

import unreal

from listener import lookup
from listener.asset_delete import delete_unreferenced_project_asset
from listener.dispatch import register
from listener.logutil import log_msg
from listener.serialize import actor_guid, is_live

_ALLOWED_KINDS = ("actor", "asset")


def _refuse(reason: str) -> None:
    raise ValueError(f"Refused: {reason}")


def _usable_guid(guid: str) -> str:
    """Empty when UEFN handed 32 zeros — Creative devices often have no real guid."""
    compact = (guid or "").replace("{", "").replace("}", "").replace("-", "").strip()
    if not compact or set(compact) <= {"0"}:
        return ""
    return guid.strip()


def _resolve_actor(ident: str, guid: str):
    """Find the actor by recorded guid first; the path is only a fallback."""
    guid = _usable_guid(guid)
    if guid:
        for actor in lookup.actor_list():
            if not is_live(actor):
                continue
            try:
                if actor_guid(actor) == guid:
                    return actor
            except Exception:
                continue
        # A recorded guid that no longer exists means the actor is already gone
        # or was replaced. Deleting whatever now sits at that path would be wrong.
        _refuse(
            f"no actor with guid {guid} is in the level any more — it was already "
            "removed, or replaced by a different actor. Remove it in the Outliner if it is still there."
        )
    actor = lookup.find_actor(ident)
    if actor is None:
        _refuse(f"actor not found: {ident}")
    if guid:
        try:
            live_guid = actor_guid(actor)
        except Exception:
            live_guid = ""
        if live_guid and live_guid != guid:
            _refuse(f"the actor at {ident} is not the one that was created (guid changed)")
    return actor


@register("ducky_revert_creation")
def cmd_ducky_revert_creation(kind: str = "", id: str = "", guid: str = "") -> dict:
    """Remove one actor or asset that the change record says an agent created.

    Internal: called only by the change journal when a user reverts a run. One id
    per call, no bulk form. Assets are refused while anything still references
    them, so undoing a creation can never break something built on top of it.
    """
    kind = (kind or "").strip().lower()
    ident = (id or "").strip()
    guid = (guid or "").strip()
    if kind not in _ALLOWED_KINDS:
        _refuse(f"kind must be one of {_ALLOWED_KINDS}, got {kind!r}")
    if not ident and not guid:
        _refuse("nothing identified to remove")

    if kind == "actor":
        actor = _resolve_actor(ident, guid)
        if not is_live(actor):
            _refuse("that actor is no longer valid")
        label = ""
        try:
            label = actor.get_actor_label()
        except Exception:
            pass
        with unreal.ScopedEditorTransaction("Ducky revert creation"):
            actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            actor_sub.destroy_actor(actor)
        lookup.invalidate()
        log_msg(f"ducky_revert_creation removed actor {label or ident}", "info")
        return {"ok": True, "kind": "actor", "removed": label or ident, "guid": guid}

    return delete_unreferenced_project_asset(ident)
