"""One place that owns level saving, so a burst of edits becomes a single save.

Why this exists: every edit command used to call
``unreal.EditorLevelLibrary.save_current_level()`` inline. When an agent fired
nine device setups at once, that meant nine full level saves back-to-back —
which freezes (and can crash) UEFN. A full level save is the most expensive
operation the editor has.

Now edit commands call :func:`request_level_save` instead of saving directly.
The Slate tick calls :func:`flush_pending_save` every frame, which performs one
save once edits have settled (no new edit for ``_SETTLE_SEC``). N rapid edits
collapse into a single save, no matter which command or client triggered them.

Explicit saves (the ``save_current_level`` command) go through :func:`save_now`,
which saves immediately and clears any pending coalesced save.
"""

from __future__ import annotations

import time

import unreal

from listener.logutil import log_msg

# Save this long after the last edit. A burst of edits keeps pushing the moment
# out, so they collapse into one save when the burst ends. Small enough to feel
# instant, large enough to swallow a parallel-tool storm.
_SETTLE_SEC = 0.4

_save_pending = False
_last_edit_at = 0.0


def request_level_save() -> None:
    """Schedule a level save. A burst of these results in exactly one save."""
    global _save_pending, _last_edit_at
    _save_pending = True
    _last_edit_at = time.time()


def flush_pending_save() -> None:
    """Called once per editor tick: save once, only after edits have settled."""
    if _save_pending and time.time() - _last_edit_at >= _SETTLE_SEC:
        save_now()


def save_now() -> bool:
    """Save the level immediately and clear any pending coalesced save."""
    global _save_pending
    _save_pending = False
    try:
        return bool(unreal.EditorLevelLibrary.save_current_level())
    except Exception as exc:  # editor closing, no world, etc.
        log_msg(f"Level save failed: {exc}", "error")
        return False
