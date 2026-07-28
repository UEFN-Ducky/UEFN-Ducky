"""Shared asset-registry helpers for listener registry modules."""

from __future__ import annotations

import unreal


def assets_by_class(module_path: str, class_name: str) -> list:
    """Asset registry query that works across UE class-path API generations."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    tlap = getattr(unreal, "TopLevelAssetPath", None)
    if tlap is not None:
        try:
            return list(registry.get_assets_by_class(tlap(module_path, class_name), True))
        except Exception:
            pass
    return list(registry.get_assets_by_class(class_name, True))
