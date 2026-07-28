"""Introspection registry tools: discover the unreal API so AI code is reliable."""

from __future__ import annotations

from typing import List

import unreal

from listener.dispatch import register


def describe_class(class_name: str, max_members: int = 200) -> dict:
    """List methods, attributes, and base classes of an ``unreal`` or Verse class.

    Use before writing execute_python that touches an unfamiliar class so you
    call real methods/properties instead of guessing.

    For Verse compiled classes pass the asset path
    (``/YourProject/_Verse.Verse-Module-class_name``) or the mangled class name
    (``Verse-Module-class_name``).
    """
    cls = getattr(unreal, class_name, None)
    if cls is None and ("/" in class_name or class_name.startswith("Verse-")):
        path = class_name
        if not path.startswith("/"):
            project_file = str(unreal.Paths.get_project_file_path())
            project_name = project_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            project_name = project_name.rsplit(".", 1)[0]
            path = f"/{project_name}/_Verse.{class_name}"
        cls = unreal.load_class(None, path)
        if cls is None:
            cls = unreal.load_object(None, path)
    if cls is None:
        raise ValueError(
            f"Class not found: {class_name!r}. "
            "For Verse devices use spawn_actor(asset_path='/Project/_Verse.Verse-...') "
            "or describe_class with the full /Project/_Verse path."
        )
    methods: List[str] = []
    attributes: List[str] = []
    for name in dir(cls):
        if name.startswith("__"):
            continue
        try:
            member = getattr(cls, name)
        except Exception:
            continue
        if callable(member):
            methods.append(name)
        else:
            attributes.append(name)
    bases = [b.__name__ for b in getattr(cls, "__mro__", []) if b.__name__ != class_name]
    return {
        "class": class_name,
        "doc": (getattr(cls, "__doc__", "") or "").strip()[:1000],
        "bases": bases[:30],
        "methods": sorted(methods)[:max_members],
        "attributes": sorted(attributes)[:max_members],
        "method_count": len(methods),
        "attribute_count": len(attributes),
    }


def search_unreal_api(query: str, max_results: int = 60) -> dict:
    """Find names in the ``unreal`` module containing ``query`` (case-insensitive)."""
    if not query.strip():
        raise ValueError("query must not be empty")
    q = query.strip().lower()
    names = [n for n in dir(unreal) if q in n.lower() and not n.startswith("__")]
    names.sort()
    return {"query": query, "names": names[:max_results], "count": len(names), "truncated": len(names) > max_results}


def list_subsystems() -> dict:
    """List ``unreal`` classes whose name ends with 'Subsystem'."""
    names = sorted(n for n in dir(unreal) if n.endswith("Subsystem") and not n.startswith("__"))
    return {"subsystems": names, "count": len(names)}


def list_actor_classes(prefix: str = "", max_results: int = 200) -> dict:
    """List ``unreal`` classes that derive from Actor, optionally filtered by prefix."""
    actor_cls = unreal.Actor
    out: List[str] = []
    for n in dir(unreal):
        if prefix and not n.startswith(prefix):
            continue
        try:
            obj = getattr(unreal, n)
            if isinstance(obj, type) and issubclass(obj, actor_cls):
                out.append(n)
        except Exception:
            continue
    out.sort()
    return {"prefix": prefix, "classes": out[:max_results], "count": len(out), "truncated": len(out) > max_results}


register("describe_class")(describe_class)
register("search_unreal_api")(search_unreal_api)
register("list_subsystems")(list_subsystems)
register("list_actor_classes")(list_actor_classes)
