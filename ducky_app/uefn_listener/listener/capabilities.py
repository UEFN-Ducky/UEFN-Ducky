"""Runtime capability flags for the in-editor MCP listener."""

from __future__ import annotations

import inspect
from typing import Any, Dict


def listener_capabilities() -> Dict[str, Any]:
    """Expose wiring-related listener features for ping/status."""
    from listener.script_property_overrides import mark_verse_wiring_overrides

    sig = inspect.signature(mark_verse_wiring_overrides)
    scalar_prop_wiring = "scalar_prop" in sig.parameters

    return {
        "scalar_prop_wiring": scalar_prop_wiring,
        "features": {
            "verse_nested_class_stems": True,
            "verse_script_hash_fallback": True,
            "resize_verse_array_field": True,
            "listener_capabilities": True,
        },
    }
