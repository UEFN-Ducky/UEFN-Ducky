"""Recently opened UEFN project paths (AppData JSON — does not modify panel_settings)."""

from __future__ import annotations

import json
from pathlib import Path

from frontend.atomic_json import write_json_atomic
from frontend.deploy import resolve_uefn_project_root
from frontend.settings import default_app_data_dir

_MAX_RECENT = 20


def _store_path() -> Path:
    return default_app_data_dir() / "recent_projects.json"


def _normalize(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return str(resolve_uefn_project_root(Path(raw)))
    except (OSError, ValueError):
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw


def load_recent_projects() -> list[str]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("projects") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            norm = _normalize(item)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out[:_MAX_RECENT]
    except (json.JSONDecodeError, OSError):
        return []


def add_recent_project(path: str) -> None:
    norm = _normalize(path)
    if not norm:
        return
    items = [p for p in load_recent_projects() if p != norm]
    items.insert(0, norm)
    write_json_atomic(_store_path(), {"projects": items[:_MAX_RECENT]})
    try:
        from frontend.appdata_maintenance import prune_empty_project_dirs

        prune_empty_project_dirs()
    except Exception:
        pass


def remove_recent_project(path: str) -> None:
    norm = _normalize(path)
    if not norm:
        return
    items = [p for p in load_recent_projects() if p != norm]
    write_json_atomic(_store_path(), {"projects": items[:_MAX_RECENT]})
