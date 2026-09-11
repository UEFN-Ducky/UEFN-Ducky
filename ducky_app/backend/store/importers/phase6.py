"""Phase 5/6 importers: mcp.json → mcp_servers, ui_crashes / plugin load errors →
events, tool_captures/ → captures rows, verse_diagnostics/ → legacy (a cache; it
rebuilds from the next scan)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from backend.store.importers.phase1 import _move_to_legacy, _read_json, once
from backend.store.importers.phase4 import _jsonl_rows
from backend.store.repos import events, misc


def import_mcp_servers(root: Path) -> dict[str, Any]:
    # The MCP store owns the file's location (tests repoint it independently of the DB root).
    from backend.mcp_plugins.store import mcp_config_path

    path = mcp_config_path()
    data = _read_json(path)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {"source": str(path), "found": False}
    clean = {str(k): dict(v) for k, v in servers.items() if isinstance(v, dict)}
    misc.mcp_servers_replace(clean)
    assert misc.mcp_servers_get() == clean, "mcp.json import verification failed"
    # The file stays: it is re-exported on every save so hand edits and IDE tooling keep working.
    return {"source": str(path), "found": True, "servers": len(clean)}


def import_more_logs(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name, kind in (("ui_crashes.jsonl", "ui_crash"), ("uefn_plugin_load_errors.jsonl", "plugin_load_error")):
        path = root / name
        if not path.is_file():
            continue
        rows = _jsonl_rows(path)
        events.insert_many(kind, [{"ts": r.get("ts"), "source": r.get("plugin_id") or r.get("surface") or "",
                                   "message": r.get("error") or r.get("message") or "", "payload": r} for r in rows])
        report[kind] = len(rows)
        _move_to_legacy(root, path, "logs")
    return report


def import_captures(root: Path) -> dict[str, Any]:
    directory = root / "tool_captures"
    n = 0
    if directory.is_dir():
        for f in sorted(directory.iterdir()):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                misc.capture_add(f.name, prefix=f.name.split("_", 1)[0], nbytes=st.st_size)
                n += 1
    return {"captures": n}


def _park_or_drop(root: Path, directory: Path, store: str) -> dict[str, Any]:
    """Move a cache tree under legacy/ when it holds files; an empty tree (the
    app created the folder before the importer ran) is simply removed."""
    if not directory.is_dir():
        return {"moved": False}
    if not any(p.is_file() for p in directory.rglob("*")):
        shutil.rmtree(directory, ignore_errors=True)
        return {"moved": False, "removed_empty": True}
    _move_to_legacy(root, directory, store)
    return {"moved": True}


def retire_diagnostics_cache(root: Path) -> dict[str, Any]:
    return _park_or_drop(root, root / "verse_diagnostics", "verse_diagnostics")


def retire_perf_files(root: Path) -> dict[str, Any]:
    """perf/*.jsonl and reports are rows now (events kind ``perf`` + cache_docs);
    the old files are a cache, so they are parked under legacy/ and deleted with it."""
    return _park_or_drop(root, root / "perf", "perf")


def retire_json_backups(root: Path) -> dict[str, Any]:
    """backups/ held rotated copies of the JSON stores. Nothing writes JSON stores
    any more, so the whole tree goes with legacy/ instead of ageing out slowly."""
    return _park_or_drop(root, root / "backups", "backups")


ALL = {
    "mcp_servers": import_mcp_servers,
    "more_logs": import_more_logs,
    "captures": import_captures,
    "verse_diagnostics": retire_diagnostics_cache,
    "perf_files": retire_perf_files,
    "json_backups": retire_json_backups,
}


def ensure(name: str) -> None:
    once(name, ALL[name])
