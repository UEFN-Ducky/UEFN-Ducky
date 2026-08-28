"""UEFN-Ducky in-process desktop plugins (Store + local sideload)."""

from __future__ import annotations

from backend.uefn_plugins.host import (
    ensure_plugins_loaded,
    get_contributions,
    is_plugin_enabled,
    reload_plugins,
)
from backend.uefn_plugins.store import (
    import_plugin_from_bytes,
    list_uefn_plugins,
    set_uefn_plugin_enabled,
    uninstall_uefn_plugin,
)

__all__ = [
    "ensure_plugins_loaded",
    "get_contributions",
    "import_plugin_from_bytes",
    "is_plugin_enabled",
    "list_uefn_plugins",
    "reload_plugins",
    "set_uefn_plugin_enabled",
    "uninstall_uefn_plugin",
]
