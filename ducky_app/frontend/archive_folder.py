"""Reserved Archive folder for soft-deleted duckies."""

from __future__ import annotations

from frontend.chat_store import ChatFolder

ARCHIVE_FOLDER_ID = "archive"
ARCHIVE_FOLDER_NAME = "Archive"
ARCHIVE_SORT_ORDER = 1_000_000.0


def is_archive_folder_id(folder_id: str) -> bool:
    return (folder_id or "").strip() == ARCHIVE_FOLDER_ID


def ensure_archive_folder(folders: list[ChatFolder]) -> tuple[list[ChatFolder], bool]:
    changed = False
    archive = next((f for f in folders if f.id == ARCHIVE_FOLDER_ID), None)
    if archive is None:
        folders.append(
            ChatFolder(
                id=ARCHIVE_FOLDER_ID,
                name=ARCHIVE_FOLDER_NAME,
                parent_id="",
                sort_order=ARCHIVE_SORT_ORDER,
            )
        )
        return folders, True
    if archive.parent_id:
        archive.parent_id = ""
        changed = True
    if archive.name != ARCHIVE_FOLDER_NAME:
        archive.name = ARCHIVE_FOLDER_NAME
        changed = True
    if archive.sort_order < ARCHIVE_SORT_ORDER:
        archive.sort_order = ARCHIVE_SORT_ORDER
        changed = True
    return folders, changed


def assert_not_archive_folder(folder_id: str, *, action: str) -> None:
    if is_archive_folder_id(folder_id):
        raise ValueError(f"Cannot {action} the Archive folder")
