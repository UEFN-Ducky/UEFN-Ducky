"""Fortnite actor discovery registry tools (read-only inspection of Fort* actors)."""

from __future__ import annotations

from typing import List, Optional

from listener import lookup, paging
from listener.dispatch import register
from listener.serialize import ALL_ACTOR_FIELDS, serialize, serialize_actor


def list_fort_actors(
    class_prefix: str = "Fort",
    offset: int = 0,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> dict:
    """List level actors whose class starts with Fort (devices, props, etc.), a page at a time."""
    # lookup.actor_list() already filters invalid/pending-kill actors, so get_class() is safe.
    field_list = [f for f in (fields or []) if f in ALL_ACTOR_FIELDS] or None
    paged = paging.page(
        lookup.actor_list(),
        lambda a: serialize_actor(a, field_list),
        offset=offset,
        limit=limit,
        keep=lambda a: a.get_class().get_name().startswith(class_prefix),
    )
    return paging.reply("actors", paged, class_prefix=class_prefix)


def get_fort_actor_info(actor_path: str, properties: Optional[List[str]] = None) -> dict:
    """Inspect a Fort* actor (read-only properties)."""
    actor = lookup.require_actor(actor_path)
    cls_name = actor.get_class().get_name()
    if not cls_name.startswith("Fort"):
        raise ValueError(f"Actor is not a Fort* class: {cls_name}")
    props = properties or ["bHidden", "ActorLabel", "FolderPath", "Tags"]
    out = {}
    for prop in props:
        try:
            out[prop] = serialize(actor.get_editor_property(prop))
        except Exception as e:
            out[prop] = f"<error: {e}>"
    return {"actor_path": actor.get_path_name(), "label": actor.get_actor_label(), "class": cls_name, "properties": out}


register("list_fort_actors")(list_fort_actors)
register("get_fort_actor_info")(get_fort_actor_info)
