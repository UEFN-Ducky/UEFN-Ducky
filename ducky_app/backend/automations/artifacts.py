"""Per-chat AppData folders for pipeline run files.

Never ``workflow/`` or a shared ``pipelines/`` dump — each chat owns
``%LOCALAPPDATA%/UEFN-Ducky/chats/<conv_id>/``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from frontend.app_paths import resolve_app_data_dir


def _safe_id(raw: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in (raw or "").strip())
    return (cleaned[:80] or "unknown")


def chat_dir(conv_id: str, *, for_write: bool = True) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "chats" / _safe_id(conv_id)
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def caller_run_dir(conv_id: str, run_id: str) -> Path:
    path = chat_dir(conv_id) / _safe_id(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_files(folder: Path) -> list[dict[str, str]]:
    if not folder.is_dir():
        return []
    out: list[dict[str, str]] = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            out.append({"path": str(p), "name": p.name})
    return out


def copy_files_into(files: Any, dest: Path) -> list[dict[str, str]]:
    dest.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, str]] = []
    for raw in files or []:
        src = Path(raw["path"] if isinstance(raw, dict) else raw)
        if not src.is_file():
            continue
        target = dest / src.name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        out.append({"path": str(target), "name": target.name})
    return out
