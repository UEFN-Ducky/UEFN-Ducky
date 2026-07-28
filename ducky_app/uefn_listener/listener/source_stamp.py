"""Fingerprint of the listener source tree, captured at import time.

``LOADED_STAMP`` describes the code that is actually RUNNING (computed when the
package is imported / re-imported by reload_listener). The MCP host computes the
same stamp over the AppData source tree on disk; a mismatch means "newer code is
deployed but not loaded" and triggers an automatic reload_listener. Keep the
algorithm in sync with ``frontend/deploy.py:listener_tree_stamp``.
"""

from __future__ import annotations

import os
from pathlib import Path


def compute_stamp(root: Path) -> str:
    """``"<py file count>:<max mtime>"`` over the tree (skips __pycache__)."""
    latest = 0.0
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                try:
                    m = (Path(dirpath) / name).stat().st_mtime
                except OSError:
                    continue
                count += 1
                if m > latest:
                    latest = m
    except OSError:
        pass
    return f"{count}:{latest:.0f}"


_PACKAGE_ROOT = Path(__file__).resolve().parent

LOADED_STAMP = compute_stamp(_PACKAGE_ROOT)
