"""Chats made on this PC before signing in: the sidebar lists them under the account's own,
and the person adds them to the account or deletes them.

They were only hidden: chats belong to an account, so a sign-in showed the account's and
nothing of what was made signed out (``backend.store.repos.local_chats``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frontend.ui_web.panel_api as _pa

_log = logging.getLogger(__name__)
_LOCKED = "Your account's data key isn't on this PC yet. Go online and try again."


def _repo():
    """The store's functions, or None while chats are files (then there are no accounts)."""
    from frontend.ui_web.chats import store

    if not store._use_db():
        return None
    store._repo()  # the one-time import of older chats has run
    from backend.store.repos import local_chats

    return local_chats


def _chat_folders(conv_id: str, projects: list[str]) -> list[Path]:
    from frontend.ui_web.chats import store

    root = store._chats_root()
    return [root / project / "conversations" / conv_id for project in dict.fromkeys(projects) if project]


def list_local_chats(self) -> dict[str, Any]:
    """``{ok, chats: [{id, title, updated, chats, group}]}``: what was made signed out on
    this PC, for the signed-in account. Empty signed out, and never an error: the sidebar
    section simply isn't there."""
    del self
    try:
        repo = _repo()
        return {"ok": True, "chats": repo.waiting() if repo is not None else []}
    except Exception:  # noqa: BLE001 — listing them must never get in the way of the sidebar
        _log.warning("could not list the chats made signed out", exc_info=True)
        return {"ok": True, "chats": []}


def adopt_local_chats(self, conv_ids: list[str] | None = None) -> dict[str, Any]:
    """Move the named chats made signed out (each with its sub-agent chats or its whole
    group) into the signed-in account; every one with ``None``. ``{ok, moved, failed}``."""
    from backend.uefn_plugins import data_crypto
    from frontend.ui_web.chats import folders, store

    repo = _repo()
    if repo is None:
        return {"ok": True, "moved": [], "failed": []}
    ids = None if conv_ids is None else [str(i) for i in conv_ids if i]
    try:
        out = repo.adopt(ids)
    except data_crypto.Locked:
        return {"ok": False, "error": _LOCKED, "moved": [], "failed": []}
    except Exception as exc:  # noqa: BLE001 — nothing moved; the chats are where they were
        _log.exception("moving the chats made signed out failed")
        return {"ok": False, "error": str(exc) or "Couldn't add them to your account.", "moved": [], "failed": []}
    moved = list(out.get("moved") or [])
    if moved:
        from backend.store import sealed_files
        from backend.store.repos import chats

        beside = False
        for conv_id in moved:
            project = chats.conv_project_id(conv_id) or ""
            beside = beside or project != store.ONE_LIST_SLUG
            for folder in _chat_folders(conv_id, [project, store.ONE_LIST_SLUG]):
                sealed_files.open_local_folder(folder / "attachments")
        if beside:
            # Still stored under a project from before the one list: the next sidebar load
            # moves it in, like every older row.
            folders._one_list_done.discard(folders._one_list_key())
        _pa.notify_chats_changed(open_tab=False)
    return {"ok": not out.get("failed") or bool(moved), "moved": moved, "failed": list(out.get("failed") or [])}


def delete_local_chats(self, conv_ids: list[str]) -> dict[str, Any]:
    """Delete the named chats made signed out for good, each with what belongs to it.
    ``{ok, deleted}``."""
    del self
    repo = _repo()
    if repo is None:
        return {"ok": True, "deleted": []}
    try:
        rows = repo.remove([str(i) for i in conv_ids or [] if i])
    except Exception as exc:  # noqa: BLE001
        _log.exception("deleting the chats made signed out failed")
        return {"ok": False, "error": str(exc) or "Couldn't delete them.", "deleted": []}
    from frontend.ui_web.chats import conversations, store

    for conv_id, project in rows:
        try:
            from backend.tools.panel.permission_prompt import forget_chat

            forget_chat(conv_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            from backend.sandbox import paths as sandbox_paths

            if sandbox_paths.exists(conv_id):
                from backend.sandbox import service as sandbox_service

                sandbox_service.clear(conv_id)
        except Exception:  # noqa: BLE001
            pass
        for folder in _chat_folders(conv_id, [project, store.ONE_LIST_SLUG]):
            try:
                if folder.is_dir():
                    conversations._remove_chat_folder(conv_id, folder)
            except OSError:
                _log.warning("a deleted chat's folder was left behind", exc_info=True)
    deleted = [conv_id for conv_id, _project in rows]
    if deleted:
        _pa.notify_chats_changed(open_tab=False, removed_conv_ids=deleted)
    return {"ok": True, "deleted": deleted}
