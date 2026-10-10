"""The Workflows editor's own settings on this PC (grid, snap, tool, panel sizes, panel
zoom, folded list). Kept in AppData so they survive a WebView storage wipe, like the
workspace dock layout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir

_KEYS = ("grid", "snap", "tool", "collapsed", "panelWidths", "panelZoom")
_MAX_BYTES = 16_000


def _path() -> Path:
    return resolve_app_data_dir(for_write=True) / "workflow_editor.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in _KEYS if isinstance(data, dict) and key in data}


def save(prefs: dict[str, Any]) -> dict[str, Any]:
    """Merge the known keys into what is saved; anything else is ignored."""
    from frontend.atomic_json import write_json_atomic

    current = load()
    merged = {**current, **{key: prefs[key] for key in _KEYS if key in (prefs or {})}}
    if len(json.dumps(merged)) > _MAX_BYTES:
        raise ValueError("Editor settings are too large")
    # The view saves what it just loaded on every mount, resize and zoom; an
    # unchanged save would still back up, fsync and replace the file.
    if merged != current:
        write_json_atomic(_path(), merged)
    return merged
