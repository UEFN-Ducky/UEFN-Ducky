"""Run every importer once at boot (ADR 0003 §7).

Each store's importer is also triggered lazily on first use, but an upgrade
must leave the AppData folder fully transitioned after the *first* boot: every
legacy file either imported (and moved under ``legacy/``) or untouched because
it never existed. Only then is the three-clean-boot countdown that deletes
``legacy/`` meaningful. All importers are idempotent (a ``meta`` flag per
importer per database) and serialised by one lock, so running them here and
lazily is safe.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("uefn_ducky.store.boot")


def ensure_all_stores() -> dict[str, Any]:
    """Import everything that has not been imported into this database yet.

    Returns ``{importer_name: report_or_None}`` (None = already done earlier)."""
    from backend.store.switch import use_db

    if not use_db("settings"):
        return {}
    out: dict[str, Any] = {}
    from backend.store.importers import phase1, phase2, phase3, phase4, phase6

    steps: list[tuple[str, Any]] = []
    for name in phase1.ALL:
        steps.append((name, lambda n=name: phase1.once(n, phase1.ALL[n])))
    steps.append(("chats", lambda: phase1.once("chats", phase2.import_chats)))
    steps.append(("ledger", lambda: phase1.once("ledger", phase3.import_ledger_and_history)))
    for name in phase4.ALL:
        steps.append((name, lambda n=name: phase1.once(n, phase4.ALL[n])))
    for name in phase6.ALL:
        steps.append((name, lambda n=name: phase1.once(n, phase6.ALL[n])))
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 — one bad legacy file must not block the rest
            _log.exception("store import %s failed", name)
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    # Per-project ``.ducky/`` folders (plans, tasks) for every project we know of
    # and can reach; the lazy fold on project open still covers the rest.
    try:
        from frontend.ui_web.recent_projects import load_recent_projects

        for path in load_recent_projects():
            if path and _has_dot_ducky(path):
                rep = phase4.ensure_project(str(path))
                if rep is not None:
                    out[f"project_dotducky:{path}"] = rep
    except Exception:  # noqa: BLE001
        _log.exception("per-project fold at boot failed")
    _prune_empty_legacy_parents()
    return out


def _has_dot_ducky(path: object) -> bool:
    from pathlib import Path

    try:
        return (Path(str(path)) / ".ducky").is_dir()
    except OSError:
        return False


# Folders the old stores lived in. Importers move their *files* under legacy/;
# the empty parents they leave behind would otherwise sit in App Data forever.
_LEGACY_PARENTS = (
    "chats/projects", "chats", "changesets", "file_history", "workspace", "memory", "plan_templates",
    "uefn_plugin_cache", "uefn_plugin_prefs", "perf", "verse_diagnostics", "backups",
)


def _prune_empty_legacy_parents() -> None:
    import os
    from pathlib import Path

    from backend.store import db

    root = db.app_root()
    for rel in _LEGACY_PARENTS:
        top = root / rel
        if not top.is_dir():
            continue
        # bottom-up: drop every empty directory, then the top itself when empty
        for dirpath, _dirnames, _filenames in os.walk(top, topdown=False):
            if not any(Path(dirpath).iterdir()):
                try:
                    Path(dirpath).rmdir()
                except OSError:
                    pass
