"""Re-export shim: the desktop plugin host lives in ``backend.uefn_plugins.plugin_host``.

Every name this module had still reads and patches as ``host.X`` (``plugin_host.compat``):
``host.X`` is the owning module's current value, and setting it sets it in every
plugin_host module that binds it. New code imports from the owning module instead.
"""

from __future__ import annotations

from backend.uefn_plugins.plugin_host import api as _api
from backend.uefn_plugins.plugin_host import backends as _backends
from backend.uefn_plugins.plugin_host import boot as _boot
from backend.uefn_plugins.plugin_host import compat as _compat
from backend.uefn_plugins.plugin_host import contributions as _contributions
from backend.uefn_plugins.plugin_host import crash_guard as _crash_guard
from backend.uefn_plugins.plugin_host import errors as _errors
from backend.uefn_plugins.plugin_host import folders as _folders
from backend.uefn_plugins.plugin_host import gateways as _gateways
from backend.uefn_plugins.plugin_host import lifecycle as _lifecycle
from backend.uefn_plugins.plugin_host import loader as _loader
from backend.uefn_plugins.plugin_host import panel_rpc as _panel_rpc
from backend.uefn_plugins.plugin_host import services as _services
from backend.uefn_plugins.plugin_host import state as _state
from backend.uefn_plugins.plugin_host import tool_registry as _tool_registry
from backend.uefn_plugins.plugin_host import ui_registry as _ui_registry

_compat.install(
    __name__,
    (
        _state,
        _boot,
        _loader,
        _lifecycle,
        _backends,
        _crash_guard,
        _contributions,
        _ui_registry,
        _services,
        _gateways,
        _tool_registry,
        _panel_rpc,
        _folders,
        _errors,
        _api,
    ),
)
