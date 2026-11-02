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
            if path:
                lift_content_dot_ducky(str(path))
            if path and _has_dot_ducky(path):
                rep = phase4.ensure_project(str(path))
                if rep is not None:
                    out[f"project_dotducky:{path}"] = rep
    except Exception:  # noqa: BLE001
        _log.exception("per-project fold at boot failed")
    _prune_empty_legacy_parents()
    return out


def lift_content_dot_ducky(project: str) -> int:
    """Move a stray ``<island>/Content/.ducky`` to ``<island>/.ducky``: UEFN submits all of
    Content, so Ducky's notes there blocked commits. A name already taken goes under
    ``.ducky/from-content/``. Returns the number of files moved; never raises."""
    import os
    import shutil

    from backend.workspace.paths import island_root

    island = island_root(project)
    ignore_dot_ducky(island)
    src = os.path.join(island, "Content", ".ducky")
    if not os.path.isdir(src):
        return 0
    dst = os.path.join(island, ".ducky")
    moved = 0
    for dirpath, _dirs, files in os.walk(src):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, src)
            target = os.path.join(dst, rel)
            if os.path.exists(target):
                target = os.path.join(dst, "from-content", rel)
            if os.path.exists(target):
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(full, target)
                moved += 1
            except OSError:
                _log.warning("could not move %s out of Content", full)
    for dirpath, _dirs, _files in os.walk(src, topdown=False):
        try:
            os.rmdir(dirpath)  # empty folders only
        except OSError:
            pass
    if moved:
        _log.info("moved %d files from %s to %s", moved, src, dst)
    return moved


_DOT_DUCKY_RULES = {".ducky", ".ducky/", "/.ducky", "/.ducky/", "**/.ducky", "**/.ducky/"}


def ignore_dot_ducky(island: str) -> bool:
    """Keep ``<island>/.ducky`` out of UEFN revision control: it is one person's Ducky notes
    (plans, swarms, memory), never island content. Adds ``.ducky`` to the island's existing
    ``.loreignore`` / ``.urcignore``. Returns True when a file changed; never raises."""
    import os

    changed = False
    for name in (".loreignore", ".urcignore"):
        path = os.path.join(island, name)
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue  # ponytail: only files UEFN made; a workspace without one isn't created here
        if any(line.strip() in _DOT_DUCKY_RULES for line in text.splitlines()):
            continue
        sep = "" if not text or text.endswith("\n") else "\n"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{sep}\n# UEFN Ducky: per-person notes, never submitted\n.ducky\n")
            changed = True
        except OSError:
            _log.warning("could not add .ducky to %s", path)
    return changed


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
    "tasks", "mcp_plugins",
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
