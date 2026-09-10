"""Per-store backend switch (rollback lever, ADR 0003 §7).

``DUCKY_STORE_BACKEND=files`` sends every migrated store back to its legacy
files; ``DUCKY_STORE_BACKEND_SETTINGS=files`` does it for one store. Default is
the database. Read at call time so a test can flip it with ``monkeypatch``.
"""

from __future__ import annotations

import os

STORES = ("settings", "secrets", "projects", "workspace_state", "cache_docs", "plugin_kv")


def use_db(store: str) -> bool:
    specific = os.environ.get(f"DUCKY_STORE_BACKEND_{store.upper()}", "").strip().lower()
    if specific:
        return specific != "files"
    return os.environ.get("DUCKY_STORE_BACKEND", "").strip().lower() != "files"
