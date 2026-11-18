"""Remove install staging folders a crashed install left behind."""

from __future__ import annotations

import logging
import time
from pathlib import Path

_log = logging.getLogger(__name__)


def sweep(staging_dir: Path, max_age_hours: float = 24, *, prefix: str | tuple[str, ...] = "") -> int:
    """Delete entries of ``staging_dir`` (whose name starts with ``prefix``) older than
    ``max_age_hours``. A running install's folder is younger, so it stays. Never raises;
    returns how many went."""
    from backend.uefn_plugins.store import _rmtree_retry

    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        entries = list(Path(staging_dir).iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if not entry.name.startswith(prefix) or entry.stat().st_mtime > cutoff:
                continue
            if entry.is_dir():
                _rmtree_retry(entry, attempts=3, ignore_final=True)
            else:
                entry.unlink(missing_ok=True)
            removed += not entry.exists()
        except Exception:  # noqa: BLE001 — a locked leftover never fails the install
            _log.warning("could not remove staging leftover %s", entry.name, exc_info=True)
    return removed
