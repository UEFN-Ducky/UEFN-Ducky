"""Build and apply Verse ``ScriptPropertyOverrides`` blobs for UEFN Ducky.

Creative device refs (``player_spawner_device`` -> spawn pad) and optional
``Assets_texture`` icon paths are serialized in the actor-level
``ScriptPropertyOverrides`` byte array (protected from normal Python reads on
many builds; ``set_editor_property`` on the actor also fails on current UEFN).

**Do NOT patch ``__ExternalActors__/*.uasset`` files on disk.** Replacing
``None`` placeholder bytes in the middle of a serialized UObject invalidates
internal size/count fields and causes ``Serial size mismatch`` crashes on load
(LinkerLoad.cpp). There is no safe raw-byte edit path without a full uasset
writer that updates all export tables.

Record layout (reverse-engineered from ``ValkyrieUploadTemp`` external actors):

    u32 len, utf-8 string + NUL   (repeated)
"""

from __future__ import annotations

import struct
from typing import Any, List, Optional, Sequence

import unreal


def _pack_str(value: str) -> bytes:
    raw = value.encode("utf-8") + b"\x00"
    return struct.pack("<I", len(raw)) + raw


def build_creative_device_link_record(
    wrapper_path: str,
    package_path: str,
    package_name: str,
    target_actor_path: str,
    verse_module: str,
    verse_class: str,
) -> bytes:
    """One ``player_spawner_device`` wrapper -> ``FortCreativeDeviceProp`` link."""
    parts = (
        wrapper_path,
        package_path,
        package_name,
        target_actor_path,
        verse_module,
        verse_class,
        "",
    )
    return b"".join(_pack_str(p) for p in parts)


def build_texture_path_record(
    struct_path: str,
    assets_texture_path: str,
    package_path: str,
    package_name: str,
    texture_asset_path: str,
    verse_assets_module: str,
    assets_texture_class: str,
) -> bytes:
    """Link ``Assets_texture`` subobject to a ``/Project/Textures/...`` asset."""
    parts = (
        struct_path,
        assets_texture_path,
        package_path,
        package_name,
        texture_asset_path,
        verse_assets_module,
        assets_texture_class,
        "",
    )
    return b"".join(_pack_str(p) for p in parts)


def _project_paths() -> tuple[str, str]:
    """Return ``(<world package path>, <world name>)`` for the open editor level.

    Was hardcoded to the ``/MCPTest/MCPTest`` dev-project mount; derive it instead so
    this works in any UEFN project.
    """
    try:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        pkg_name = world.get_package().get_name()
        if pkg_name:
            return pkg_name, pkg_name.rsplit("/", 1)[-1]
    except Exception:
        pass
    try:
        project_file = str(unreal.Paths.get_project_file_path())
        name = project_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        if name:
            return f"/{name}/{name}", name
    except Exception:
        pass
    return "/MCPTest/MCPTest", "MCPTest"


def _short_actor_path(actor: unreal.Actor) -> str:
    path = actor.get_path_name()
    if ".MCPTest:PersistentLevel." in path:
        return "PersistentLevel." + path.split(".MCPTest:PersistentLevel.", 1)[-1]
    if ":PersistentLevel." in path:
        return "PersistentLevel." + path.split(":PersistentLevel.", 1)[-1]
    return path


def _try_set_blob(actor: unreal.Actor, blob: bytes) -> tuple[bool, str]:
    """Attempt to write ``ScriptPropertyOverrides`` using several payload types."""
    errors: List[str] = []
    payloads: Sequence[Any] = (
        blob,
        bytearray(blob),
        list(blob),
    )
    for payload in payloads:
        try:
            actor.set_editor_property("ScriptPropertyOverrides", payload)
            actor.modify()
            return True, type(payload).__name__
        except Exception as exc:
            errors.append(f"{type(payload).__name__}: {exc}")
    return False, "; ".join(errors[:3])


def apply_spawner_links(manager: unreal.Actor, spawn_pads: Sequence[unreal.Actor]) -> dict:
    """Wire spawn pad actors to ``AllPlayerSpawners`` wrapper subobjects.

    Each ``player_spawner_device`` wrapper exposes ``SavedActor`` (ObjectProperty).
    Assign the ``BP_Creative_Player_Spawner_Prop_C`` actor directly — same field the
    Details panel writes (visible in T3D as ``SavedActor=...`` on the wrapper).

    Caller (``wire_player_spawners``) owns the ``ScopedEditorTransaction`` — this
    function must not open its own, or the nested transaction breaks single-undo.
    """
    script = manager.get_editor_property("Script")
    prop = "__verse_0xE027EF92_AllPlayerSpawners"
    ps_cls = unreal.load_class(None, "/CRD_PlayerSpawn/_Verse.player_spawner_device")

    wrappers = []
    links = []
    for pad in spawn_pads:
        wrapper = unreal.new_object(ps_cls, script)
        wrapper.set_editor_property("SavedActor", pad)
        wrapper.modify()
        wrappers.append(wrapper)
        links.append(
            {
                "wrapper": str(wrapper.get_fname()),
                "spawn_pad": pad.get_actor_label(),
                "saved_actor": str(wrapper.get_editor_property("SavedActor")),
            }
        )
    script.set_editor_property(prop, wrappers)
    script.modify()

    script_name = unreal.Name(str(script.get_fname()))
    manager.add_unique_transient_override(script_name, unreal.Name(prop))
    manager.modify()

    return {
        "wrapper_count": len(wrappers),
        "links": links,
        "spawn_pads": [p.get_actor_label() for p in spawn_pads],
        "ok": all(
            w.get_editor_property("SavedActor") is not None for w in wrappers
        ),
    }


def _normalize_texture_object_path(texture_spec: str) -> str:
    """Return ``/Mount/Path/Asset.Asset`` from a path or bare asset name."""
    spec = texture_spec.strip().replace("\\", "/")
    if spec.startswith("/"):
        path = spec
    else:
        name = spec.split(".")[0]
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        ar_filter = unreal.ARFilter(
            package_paths=["/Game"],
            recursive_paths=True,
            class_names=["Texture2D"],
        )
        matches: List[str] = [
            f"{asset_data.package_name}.{asset_data.asset_name}"
            for asset_data in registry.get_assets(ar_filter) or []
            if str(asset_data.asset_name) == name
        ]
        if not matches:
            raise ValueError(
                f"Texture {name!r} not found. Pass full path like "
                f"/YourProject/Textures/{name}.{name} or use search_assets."
            )
        if len(matches) > 1:
            project_file = str(unreal.Paths.get_project_file_path())
            project_name = project_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            project_name = project_name.rsplit(".", 1)[0]
            preferred = f"/{project_name}/Textures/{name}."
            matches.sort(
                key=lambda p: (
                    0 if preferred in p else 1,
                    0 if f"/{project_name}/" in p else 1,
                    p,
                )
            )
        path = matches[0]
    if "." not in path.rsplit("/", 1)[-1]:
        base = path.rsplit("/", 1)[-1]
        path = f"{path}.{base}"
    return path


def _load_texture2d(texture_spec: str) -> unreal.Texture2D:
    path = _normalize_texture_object_path(texture_spec)
    obj = unreal.load_object(None, path)
    if not obj:
        raise ValueError(f"Failed to load texture: {path}")
    return obj


def _verse_device_actor(owner: Any) -> Optional[unreal.Actor]:
    node = owner
    while node and getattr(node, "get_class", None):
        if node.get_class().get_name() == "VerseDevice_C":
            return node
        node = node.get_outer()
    return None


_ASSETS_TEXTURE_CLS = "/VerseEngine/_Verse/VNI/VerseAssets.Assets_texture"
_TEXTURE_SLOT_ORDER = (
    "Assets_texture_0",
    "Assets_texture_1",
    "Assets_texture_2",
    "Assets_texture_3",
)


def _assets_texture_children(owner_struct: Any) -> dict[str, Any]:
    icls = unreal.load_class(None, _ASSETS_TEXTURE_CLS)
    by_name: dict[str, Any] = {}
    try:
        for child in owner_struct.get_children():
            if child.get_class() == icls:
                by_name[str(child.get_fname())] = child
    except Exception:
        pass
    return by_name


def _texture_slot_in_use(owner_struct: Any, wrapper: Any, icon_prop: str) -> bool:
    try:
        if owner_struct.get_editor_property(icon_prop) == wrapper:
            return False
    except Exception:
        pass
    try:
        return wrapper.get_editor_property("AssetForEditor") is not None
    except Exception:
        return False


def _find_assets_texture_wrapper(owner_struct: Any, icon_prop: str) -> Any:
    """Return existing ``Assets_texture`` for *icon_prop* or bind an empty slot."""
    icls = unreal.load_class(None, _ASSETS_TEXTURE_CLS)
    try:
        wrapper = owner_struct.get_editor_property(icon_prop)
        if wrapper is not None:
            return wrapper
    except Exception:
        pass

    by_name = _assets_texture_children(owner_struct)
    for slot in _TEXTURE_SLOT_ORDER:
        candidate = by_name.get(slot)
        if candidate is not None and not _texture_slot_in_use(owner_struct, candidate, icon_prop):
            owner_struct.set_editor_property(icon_prop, candidate)
            return candidate

    for slot, candidate in sorted(by_name.items()):
        if not _texture_slot_in_use(owner_struct, candidate, icon_prop):
            owner_struct.set_editor_property(icon_prop, candidate)
            return candidate

    wrapper = unreal.new_object(icls, owner_struct)
    owner_struct.set_editor_property(icon_prop, wrapper)
    return wrapper


def _mark_texture_transient_overrides(
    actor: unreal.Actor,
    owner_struct: Any,
    icon_prop: str,
    array_prop: Optional[str] = None,
) -> None:
    """Mark save overrides on struct/array objects the VerseDevice can resolve by name.

    Never call ``add_unique_transient_override`` with nested ``Assets_texture_*``
    names — they are subobjects of struct rows (``currency_config_0``), not direct
    children of the device, and trigger ``AddToSaveContext failed to find object``.
    """
    script = actor.get_editor_property("Script")
    script_name = unreal.Name(str(script.get_fname()))
    struct_name = unreal.Name(str(owner_struct.get_fname()))
    icon_name = unreal.Name(icon_prop)

    actor.add_unique_transient_override(struct_name, icon_name)
    if array_prop:
        actor.add_unique_transient_override(script_name, unreal.Name(array_prop))


def assign_texture_to_assets_wrapper(
    owner_struct: Any,
    icon_prop: str,
    texture_spec: str,
    *,
    array_prop: Optional[str] = None,
) -> dict:
    """Create/reuse ``Assets_texture`` on *owner_struct* and set ``AssetForEditor``."""
    tex = _load_texture2d(texture_spec)
    obj_path = _normalize_texture_object_path(texture_spec)

    wrapper = _find_assets_texture_wrapper(owner_struct, icon_prop)
    wrapper.set_editor_property("AssetForEditor", tex)
    wrapper.modify()
    owner_struct.modify()

    actor = _verse_device_actor(owner_struct)
    if actor:
        _mark_texture_transient_overrides(actor, owner_struct, icon_prop, array_prop)
        actor.modify()

    linked = wrapper.get_editor_property("AssetForEditor")
    return {
        "icon_property": icon_prop,
        "texture_path": obj_path,
        "assets_texture": str(wrapper.get_fname()),
        "asset_for_editor": str(linked) if linked else None,
        "ok": linked is not None,
    }


def apply_texture_icon(
    owner_struct: Any,
    icon_prop: str,
    texture_asset: str,
    *,
    array_prop: Optional[str] = None,
) -> dict:
    """Assign ``Assets_texture`` on a Verse struct. *icon_prop* must be the mangled name."""
    result = assign_texture_to_assets_wrapper(
        owner_struct, icon_prop, texture_asset, array_prop=array_prop
    )
    result["icon_property"] = icon_prop
    return result


def mark_verse_wiring_overrides(
    device: unreal.Actor,
    *,
    script: Any = None,
    scalar_prop: Optional[str] = None,
    array_prop: Optional[str] = None,
    wrappers: Optional[Sequence[Any]] = None,
    link_prop: str = "SavedActor",
) -> None:
    """Mark transient save overrides so VerseDevice wiring persists on save.

    Only mark properties on the Script subobject (``Verse-*_0`` + mangled field name).
    Wrapper subobjects (``button_device``, ``Devices_creative_prop_N``, etc.) live under
    Script, not under ``VerseDevice_C`` — marking them by fname triggers
    ``AddToSaveContext failed to find object`` on save.
    """
    del wrappers, link_prop  # legacy params; do not mark nested wrapper fnames
    if script is None:
        script = device.get_editor_property("Script")
    script_name = unreal.Name(str(script.get_fname()))
    if array_prop:
        device.add_unique_transient_override(script_name, unreal.Name(array_prop))
    if scalar_prop:
        device.add_unique_transient_override(script_name, unreal.Name(scalar_prop))
    device.modify()
