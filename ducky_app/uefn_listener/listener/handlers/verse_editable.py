"""Verse @editable property commands."""

from typing import Any

from listener.bulk_removed import bulk_removed_error
from listener.dispatch import register
from listener.verse_editable_editor import (
    get_verse_editables,
    list_verse_property_hashes,
    list_verse_reference_types,
    set_currency_config_entries,
    set_verse_editable,
    set_verse_texture_icon,
    resize_verse_array_field,
    patch_verse_array_entry,
    wire_player_spawners,
    wire_verse_device_array,
    wire_verse_device_ref,
    wire_verse_prop_assets,
)


@register("list_verse_property_hashes")
def cmd_list_verse_property_hashes(refresh: bool = False) -> dict:
    return list_verse_property_hashes(refresh=refresh)


@register("get_verse_editables")
def cmd_get_verse_editables(actor_path: str, include_wiring_hints: bool = True) -> dict:
    return get_verse_editables(actor_path, include_wiring_hints=include_wiring_hints)


@register("set_verse_editable")
def cmd_set_verse_editable(
    actor_path: str,
    field: str,
    target_path: str = "",
    value: Any = None,
) -> dict:
    return set_verse_editable(actor_path, field, target_path=target_path, value=value)


@register("bulk_set_verse_editables")
def cmd_bulk_set_verse_editables(actor_path: str, properties: dict) -> dict:
    return bulk_removed_error()


@register("bulk_wire_verse_device")
def cmd_bulk_wire_verse_device(
    actor_path: str,
    wiring: dict,
    skip_missing: bool = False,
    save_level: bool = False,
    verify: bool = False,
) -> dict:
    return bulk_removed_error()


@register("setup_verse_device")
def cmd_setup_verse_device(
    asset_path: str,
    label: str,
    wiring: dict,
    location: list[float] | None = None,
    folder: str = "",
    spawn_if_exists: str = "skip",
    save_level: bool = True,
    verify: bool = True,
    skip_missing: bool = False,
) -> dict:
    return bulk_removed_error()


@register("wire_player_spawners")
def cmd_wire_player_spawners(
    manager_path: str,
    spawn_pad_paths: list[str] | None = None,
) -> dict:
    return wire_player_spawners(manager_path, spawn_pad_paths=spawn_pad_paths)


@register("set_verse_texture_icon")
def cmd_set_verse_texture_icon(
    actor_path: str,
    icon_field: str,
    texture_path: str,
    array_field: str = "",
    entry_index: int = 0,
) -> dict:
    return set_verse_texture_icon(
        actor_path,
        icon_field,
        texture_path,
        array_field=array_field,
        entry_index=entry_index,
    )


@register("list_verse_reference_types")
def cmd_list_verse_reference_types() -> dict:
    return list_verse_reference_types()


@register("wire_verse_device_ref")
def cmd_wire_verse_device_ref(
    actor_path: str,
    field: str,
    target_path: str,
) -> dict:
    return wire_verse_device_ref(actor_path, field, target_path)


@register("wire_verse_device_array")
def cmd_wire_verse_device_array(
    actor_path: str,
    field: str,
    target_paths: list[str],
) -> dict:
    return wire_verse_device_array(actor_path, field, target_paths)


@register("wire_verse_prop_assets")
def cmd_wire_verse_prop_assets(
    actor_path: str,
    field: str,
    asset_paths: list[str],
) -> dict:
    return wire_verse_prop_assets(actor_path, field, asset_paths)


@register("set_currency_config_entries")
def cmd_set_currency_config_entries(
    actor_path: str,
    count: int = 0,
    entries: list[dict] | None = None,
) -> dict:
    return set_currency_config_entries(actor_path, count=count, entries=entries)


@register("resize_verse_array_field")
def cmd_resize_verse_array_field(actor_path: str, array_field: str, count: int) -> dict:
    return resize_verse_array_field(actor_path, array_field, count)


@register("patch_verse_array_entry")
def cmd_patch_verse_array_entry(
    actor_path: str,
    array_field: str,
    index: int,
    properties: dict,
) -> dict:
    return patch_verse_array_entry(actor_path, array_field, index, properties)
