"""Public facade kept for Store plugins: ``from backend.skills.store import appdata_dir``.

Ducky's own code imports the owning modules (``backend.skills.discovery`` and friends);
only shipped plugin zips still use this path, so it carries that one name.
"""

from __future__ import annotations

from backend.skills.discovery import appdata_dir

__all__ = ["appdata_dir"]
