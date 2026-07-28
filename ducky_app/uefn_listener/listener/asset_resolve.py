"""Resolve loaded assets through ObjectRedirector chains.

UEFN's Python API does not expose ``unreal.ObjectRedirector`` as a type, and
``get_editor_property('destination_object')`` fails on redirectors. Destination
is available via asset-registry / package tags (``DestinationObject``).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import unreal

_SOFT_PATH_RE = re.compile(
    r"(?:/Script/[^']+')?(?P<path>/[^']+?)(?:\.(?P<object>[^']+))?'?$"
)


def _class_name(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        cls = obj.get_class()
        if cls is not None:
            return str(cls.get_name() or "")
    except Exception:
        pass
    try:
        return type(obj).__name__
    except Exception:
        return ""


def is_object_redirector(obj: Any) -> bool:
    return _class_name(obj).lower() == "objectredirector"


def _package_path_from_soft(text: str) -> str:
    """``/Script/Engine.StaticMesh'/Game/Foo.Foo'`` → ``/Game/Foo``."""
    raw = (text or "").strip()
    if not raw or raw.lower() in {"none", "null"}:
        return ""
    m = _SOFT_PATH_RE.search(raw.replace('\\', '/'))
    if not m:
        # Plain package path already
        p = raw.strip("'\"")
        if "." in p.rsplit("/", 1)[-1]:
            p = p.rsplit(".", 1)[0]
        return p if p.startswith("/") else ""
    path = m.group("path") or ""
    return path


def _destination_from_tags(asset_path: str) -> str:
    path = (asset_path or "").strip()
    if not path:
        return ""
    # Package tags (works when AssetData tags are empty on some builds).
    try:
        tags = unreal.EditorAssetLibrary.get_tag_values(path) or {}
        for key, value in tags.items():
            if str(key) in {"DestinationObject", "RedirectorDestinationObject"}:
                dest = _package_path_from_soft(str(value))
                if dest:
                    return dest
    except Exception:
        pass
    try:
        data = unreal.EditorAssetLibrary.find_asset_data(path)
        if data is not None:
            for tag in ("DestinationObject", "RedirectorDestinationObject"):
                try:
                    dest = _package_path_from_soft(str(data.get_tag_value(tag)))
                    if dest:
                        return dest
                except Exception:
                    continue
    except Exception:
        pass
    return ""


def follow_redirector(asset: Any, *, asset_path: str = "", max_hops: int = 8) -> Any:
    """Return the destination object behind zero or more ObjectRedirectors."""
    current = asset
    path = (asset_path or "").strip()
    if not path and current is not None:
        try:
            full = str(current.get_path_name() or "")
            path = full.rsplit(".", 1)[0] if "." in full.rsplit("/", 1)[-1] else full
        except Exception:
            path = ""

    seen: set[str] = set()
    hops = 0
    while current is not None and hops < max_hops and is_object_redirector(current):
        key = path or str(id(current))
        if key in seen:
            return current
        seen.add(key)

        dest_path = _destination_from_tags(path) if path else ""
        if not dest_path or dest_path == path:
            return current

        nxt = unreal.EditorAssetLibrary.load_asset(dest_path)
        if nxt is None:
            # Soft path sometimes needs Package.Object form
            try:
                obj_name = dest_path.rsplit("/", 1)[-1]
                nxt = unreal.find_object(None, f"{dest_path}.{obj_name}")
            except Exception:
                nxt = None
        if nxt is None:
            return current
        current = nxt
        path = dest_path
        hops += 1
    return current


def load_asset_resolved(asset_path: str) -> Tuple[Any, str]:
    """Load ``asset_path``, following redirectors. Returns ``(asset, resolved_path)``."""
    path = (asset_path or "").strip()
    if not path:
        return None, ""
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        return None, path
    resolved = follow_redirector(asset, asset_path=path)
    resolved_path = path
    if resolved is not None:
        try:
            full = str(resolved.get_path_name() or "")
            if full:
                resolved_path = full.rsplit(".", 1)[0] if "." in full.rsplit("/", 1)[-1] else full
        except Exception:
            dest = _destination_from_tags(path)
            if dest:
                resolved_path = dest
    return resolved, resolved_path


def resolve_asset_class(asset_path: str, asset_class: str = "") -> Tuple[str, Optional[str]]:
    """If ``asset_class`` is ObjectRedirector, return ``(dest_class, dest_path)``.

    Otherwise return ``(asset_class, None)`` without loading when possible.
    """
    cls = (asset_class or "").strip()
    if cls.lower() != "objectredirector":
        # Still resolve if load would reveal a redirector (registry sometimes says Object).
        if cls and cls.lower() != "object":
            return cls, None

    dest_path = _destination_from_tags(asset_path)
    if dest_path and dest_path != asset_path:
        data = unreal.EditorAssetLibrary.find_asset_data(dest_path)
        if data is not None:
            try:
                if hasattr(data, "asset_class_path"):
                    dest_cls = str(data.asset_class_path.asset_name)
                else:
                    dest_cls = str(getattr(data, "asset_class", "") or "")
            except Exception:
                dest_cls = ""
            if dest_cls and dest_cls.lower() != "objectredirector":
                return dest_cls, dest_path

    asset, resolved_path = load_asset_resolved(asset_path)
    if asset is None:
        return cls or "ObjectRedirector", None
    dest_cls = _class_name(asset) or type(asset).__name__
    if dest_cls.lower() == "objectredirector":
        return "ObjectRedirector", resolved_path if resolved_path != asset_path else None
    return dest_cls, resolved_path
