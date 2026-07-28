"""Creative device settings commands."""

from typing import Any, Dict, List, Optional

from listener.bulk_removed import bulk_removed_error
from listener.device_editor import (
    get_device_settings,
    list_creative_devices,
    set_device_settings,
)
from listener.dispatch import register


@register("list_creative_devices")
def cmd_list_creative_devices(
    class_filter: str = "",
    label_filter: str = "",
    limit: int = 200,
) -> dict:
    return list_creative_devices(class_filter=class_filter, label_filter=label_filter, limit=limit)


@register("get_device_settings")
def cmd_get_device_settings(
    actor_path: str,
    include_events: bool = False,
    keys: Optional[List[str]] = None,
) -> dict:
    return get_device_settings(actor_path, include_events=include_events, keys=keys or [])


@register("set_device_settings")
def cmd_set_device_settings(
    actor_path: str,
    properties: Dict[str, Any],
    save_level: bool = False,
) -> dict:
    return set_device_settings(actor_path, properties, save_level=save_level)


@register("bulk_set_device_settings")
def cmd_bulk_set_device_settings(
    properties: Dict[str, Any],
    class_filter: str = "",
    label_filter: str = "",
    save_level: bool = False,
) -> dict:
    return bulk_removed_error()
