"""Per-project editor workspace snapshots (open tabs, splits, focus windows)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.settings import PanelSettings, default_app_data_dir
from frontend.ui_web.project_chats import project_slug

_VERSION = 1


def _workspace_root() -> Path:
    return default_app_data_dir() / "workspace" / "projects"


def _workspace_path(slug: str) -> Path:
    return _workspace_root() / slug / "editor.json"


def _default_layout(tab_ids: list[str]) -> dict[str, Any]:
    gid = "grp-default"
    active = tab_ids[-1] if tab_ids else None
    return {
        "root": {"type": "group", "groupId": gid},
        "groups": {gid: {"id": gid, "tabIds": tab_ids, "activeTabId": active}},
        "focusedGroupId": gid,
    }


def _empty_snapshot() -> dict[str, Any]:
    layout = _default_layout([])
    return {
        "version": _VERSION,
        "openTabs": [],
        "layout": layout,
        "focusWindows": [],
    }


def _normalize_tab(tab: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(tab, dict):
        return None
    kind = tab.get("kind")
    tab_id = tab.get("id")
    name = tab.get("name")
    if kind not in ("chat", "file") or not isinstance(tab_id, str) or not tab_id.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {
        "id": tab_id.strip(),
        "kind": kind,
        "name": name.strip(),
    }
    if kind == "chat":
        chat_id = tab.get("chatId")
        if not isinstance(chat_id, str) or not chat_id.strip():
            return None
        out["chatId"] = chat_id.strip()
        ducky = tab.get("duckyStyle")
        if isinstance(ducky, str) and ducky.strip():
            out["duckyStyle"] = ducky.strip()
    else:
        path = tab.get("path")
        if not isinstance(path, str) or not path.strip():
            return None
        out["path"] = path.replace("\\", "/").strip()
    return out


def _tab_from_id_and_title(tab_id: str, title: str) -> dict[str, Any] | None:
    tid = tab_id.strip()
    name = (title or tid.split(":", 1)[-1]).strip()
    if not name:
        name = tid
    if tid.startswith("chat:"):
        chat_id = tid[5:].strip()
        if not chat_id:
            return None
        return {"id": tid, "kind": "chat", "name": name, "chatId": chat_id}
    if tid.startswith("file:"):
        path = tid[5:].replace("\\", "/").strip()
        if not path:
            return None
        return {"id": tid, "kind": "file", "name": name, "path": path}
    return None


def _normalize_focus_windows(raw: Any, open_tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tab_ids = {t["id"] for t in open_tabs}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tab_ids_raw = item.get("tabIds")
        if not isinstance(tab_ids_raw, list):
            continue
        ids = [
            t.strip()
            for t in tab_ids_raw
            if isinstance(t, str)
            and t.strip()
            and (t.strip() in tab_ids or t.strip().startswith("terminal:"))
        ]
        if not ids:
            continue
        titles_raw = item.get("titles")
        titles: dict[str, str] = {}
        if isinstance(titles_raw, dict):
            for k, v in titles_raw.items():
                if isinstance(k, str) and k in ids and isinstance(v, str) and v.strip():
                    titles[k] = v.strip()
        birth_raw = item.get("birthTabId")
        birth = birth_raw.strip() if isinstance(birth_raw, str) and birth_raw.strip() in ids else ids[0]
        if birth in seen:
            continue
        seen.add(birth)
        entry: dict[str, Any] = {"birthTabId": birth, "tabIds": ids, "titles": titles}
        layout_raw = item.get("layout")
        if isinstance(layout_raw, dict):
            tab_id_set = set(ids)
            entry["layout"] = _normalize_layout(layout_raw, tab_id_set)
        out.append(entry)
    return out


def _migrate_focused_tab_ids(focused_ids: list[str], open_tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not focused_ids:
        return []
    names = {t["id"]: t["name"] for t in open_tabs}
    out: list[dict[str, Any]] = []
    for tid in focused_ids:
        out.append({"birthTabId": tid, "tabIds": [tid], "titles": {tid: names.get(tid, "")}})
    return out


def _normalize_layout(raw: Any, tab_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _default_layout(sorted(tab_ids))

    groups_raw = raw.get("groups")
    root = raw.get("root")
    focused = raw.get("focusedGroupId")
    if not isinstance(groups_raw, dict) or not isinstance(root, dict):
        return _default_layout(sorted(tab_ids))

    groups: dict[str, Any] = {}
    for gid, group in groups_raw.items():
        if not isinstance(gid, str) or not isinstance(group, dict):
            continue
        tab_ids_list = group.get("tabIds")
        if not isinstance(tab_ids_list, list):
            continue
        filtered = [tid for tid in tab_ids_list if isinstance(tid, str) and tid in tab_ids]
        active = group.get("activeTabId")
        active_tab = active if isinstance(active, str) and active in filtered else (filtered[-1] if filtered else None)
        groups[gid] = {"id": gid, "tabIds": filtered, "activeTabId": active_tab}

    if not groups:
        return _default_layout(sorted(tab_ids))

    focused_group = focused if isinstance(focused, str) and focused in groups else next(iter(groups))
    return {"root": root, "groups": groups, "focusedGroupId": focused_group}


def _normalize_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    raw_tabs = data.get("openTabs")
    open_tabs: list[dict[str, Any]] = []
    if isinstance(raw_tabs, list):
        for item in raw_tabs:
            norm = _normalize_tab(item)
            if norm is not None:
                open_tabs.append(norm)

    tab_ids = {t["id"] for t in open_tabs}

    def _hydrate_focus_tabs_from_raw() -> None:
        raw_fw = data.get("focusWindows")
        if not isinstance(raw_fw, list):
            return
        for item in raw_fw:
            if not isinstance(item, dict):
                continue
            titles_raw = item.get("titles")
            titles = titles_raw if isinstance(titles_raw, dict) else {}
            tab_ids_raw = item.get("tabIds")
            if not isinstance(tab_ids_raw, list):
                continue
            for tid_raw in tab_ids_raw:
                if not isinstance(tid_raw, str):
                    continue
                tid = tid_raw.strip()
                if not tid or tid in tab_ids:
                    continue
                title = str(titles.get(tid) or "") if isinstance(titles.get(tid), str) else ""
                norm = _tab_from_id_and_title(tid, title)
                if norm is not None:
                    open_tabs.append(norm)
                    tab_ids.add(tid)

    _hydrate_focus_tabs_from_raw()

    def _filter_ids(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            tid = item.strip()
            if not tid or tid not in tab_ids or tid in seen:
                continue
            seen.add(tid)
            out.append(tid)
        return out
        return out

    focused_ids = _filter_ids(data.get("focusedTabIds"))
    focus_windows = _normalize_focus_windows(data.get("focusWindows"), open_tabs)
    if not focus_windows and focused_ids:
        focus_windows = _migrate_focused_tab_ids(focused_ids, open_tabs)

    focus_tab_ids = {tid for fw in focus_windows for tid in fw["tabIds"]}
    main_tab_ids = tab_ids - focus_tab_ids
    main_open_tabs = [t for t in open_tabs if t["id"] in main_tab_ids]
    layout = _normalize_layout(data.get("layout"), main_tab_ids)

    return {
        "version": _VERSION,
        "openTabs": main_open_tabs,
        "layout": layout,
        "focusWindows": focus_windows,
    }


def load_editor_workspace(project_slug_value: str | None = None) -> dict[str, Any]:
    slug = (project_slug_value or "").strip()
    if not slug:
        root = PanelSettings.load().uefn_project_root.strip()
        if not root:
            return _empty_snapshot()
        slug = project_slug(root)

    path = _workspace_path(slug)
    if not path.is_file():
        return _empty_snapshot()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_snapshot()
        return _normalize_snapshot(data)
    except (json.JSONDecodeError, OSError):
        return _empty_snapshot()


def _merge_live_focus_windows(snapshot: dict[str, Any]) -> dict[str, Any]:
    from frontend.ui_web import focus_windows

    live = focus_windows.export_snapshot()
    if not live:
        return {**snapshot, "focusWindows": []}

    open_tabs = list(snapshot.get("openTabs") or [])
    tab_ids = {t["id"] for t in open_tabs if isinstance(t, dict) and isinstance(t.get("id"), str)}
    for group in live:
        for tab_id in group.get("tabIds") or []:
            if not isinstance(tab_id, str) or tab_id in tab_ids:
                continue
            title = ""
            titles = group.get("titles")
            if isinstance(titles, dict):
                title = str(titles.get(tab_id) or "")
            norm = _tab_from_id_and_title(tab_id, title)
            if norm is not None:
                open_tabs.append(norm)
                tab_ids.add(tab_id)

    snapshot = {
        **snapshot,
        "openTabs": open_tabs,
        "focusWindows": _normalize_focus_windows(live, open_tabs),
    }
    focus_tab_ids = {tid for fw in snapshot["focusWindows"] for tid in fw["tabIds"]}
    main_tab_ids = tab_ids - focus_tab_ids
    snapshot["openTabs"] = [t for t in open_tabs if t["id"] in main_tab_ids]
    snapshot["layout"] = _normalize_layout(snapshot.get("layout"), main_tab_ids)
    return snapshot


def save_editor_workspace(payload: dict[str, Any], project_root: str | None = None) -> None:
    root = project_root
    if root is None:
        root = PanelSettings.load().uefn_project_root
    slug = project_slug(root)
    if slug == "_no_project":
        return

    snapshot = _normalize_snapshot(payload if isinstance(payload, dict) else {})
    snapshot = _merge_live_focus_windows(snapshot)
    dest = _workspace_path(slug)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(dest, snapshot)


def flush_focus_windows_to_disk(project_root: str | None = None) -> None:
    """Best-effort save of live focus windows on app exit (JS flush may not finish)."""
    root = project_root
    if root is None:
        root = PanelSettings.load().uefn_project_root
    slug = project_slug(root)
    if slug == "_no_project":
        return
    path = _workspace_path(slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else _empty_snapshot()
        if not isinstance(data, dict):
            data = _empty_snapshot()
    except (json.JSONDecodeError, OSError):
        data = _empty_snapshot()
    snapshot = _merge_live_focus_windows(_normalize_snapshot(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, snapshot)
