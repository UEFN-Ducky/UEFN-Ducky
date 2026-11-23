"""Process-shared workflow run state and cancellation requests."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import psutil
from frontend.app_paths import resolve_app_data_dir

_LOCK = threading.RLock()


def _directory() -> Path:
    return resolve_app_data_dir(for_write=True) / "workflow-runs"


def _path(workflow_id: str, run_id: str, suffix: str = ".json") -> Path:
    key = hashlib.sha256(f"{workflow_id}\0{run_id}".encode()).hexdigest()
    return _directory() / (key + suffix)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def start(workflow_id: str, run_id: str) -> None:
    with _LOCK:
        _write(_path(workflow_id, run_id), {
            "id": workflow_id, "run": run_id, "pid": os.getpid(),
            "process_started": psutil.Process().create_time(),
            "started": time.time(), "state": "started", "events": [],
        })


def record(event: dict) -> None:
    if event.get("type") not in ("workflow_run", "workflow_step", "workflow_output"):
        return
    workflow_id, run_id = str(event.get("id") or ""), str(event.get("run") or "")
    if not workflow_id or not run_id:
        return
    path = _path(workflow_id, run_id)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        data["sequence"] = int(data.get("sequence", 0)) + 1
        event["workflow_sequence"] = data["sequence"]
        events = data["events"]
        key = (event["type"], event.get("node"), event.get("source"))
        events[:] = [e for e in events if
                     (e["type"], e.get("node"), e.get("source")) != key
                     or (e["type"] == "workflow_run" and e.get("state") == "started")]
        events.append(dict(event))
        if event["type"] == "workflow_run":
            data["state"] = event.get("state")
            if data["state"] != "started":
                data["ended"] = time.time()
        _write(path, data)


def _alive(data: dict) -> bool:
    try:
        process = psutil.Process(int(data["pid"]))
        return process.is_running() and process.create_time() == data["process_started"]
    except (psutil.Error, KeyError, ValueError):
        return False


# Run files already parsed, by path: ((modified, size, file id), data). Several processes write
# runs, so a file is read again only when one of those changed. Nov 11 2026: every call read and
# parsed every run of the last day (their outputs too), and the window, the team keeper and
# the updater all call it on a timer. _write replaces the file, so the file id (inode) changes
# on every write: Linux moves mtime once per kernel tick, and a same-size rewrite inside one
# tick looked unchanged. Windows' scandir reports the id as 0 (time and size decide, as before).
_PARSED: dict[str, tuple[tuple[int, int, int], dict]] = {}


def _files() -> list[tuple[Path, dict]]:
    """Each run file with its parsed data; unreadable ones are left out. Call under _LOCK."""
    try:
        entries = [e for e in os.scandir(_directory()) if e.name.endswith(".json")]
    except OSError:
        _PARSED.clear()
        return []
    found: list[tuple[Path, dict]] = []
    for entry in entries:
        try:
            stat = entry.stat()
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            kept = _PARSED.get(entry.path)
            if kept is None or kept[0] != stamp:
                data = json.loads(Path(entry.path).read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not a run")
                kept = _PARSED[entry.path] = (stamp, data)
            found.append((Path(entry.path), kept[1]))
        except (OSError, ValueError):
            _PARSED.pop(entry.path, None)
    here = {str(path) for path, _ in found}
    for gone in [key for key in _PARSED if key not in here]:
        del _PARSED[gone]
    return found


def runs(workflow_id: str = "") -> list[dict]:
    result = []
    with _LOCK:
        for path, kept in _files():
            try:
                if kept.get("ended", kept["started"]) < time.time() - 86400:
                    path.unlink(missing_ok=True)
                    path.with_suffix(".stop").unlink(missing_ok=True)
                    _PARSED.pop(str(path), None)
                    continue
                if workflow_id and kept.get("id") != workflow_id:
                    continue
                # The caller's own copy: what it changes never reaches the kept file data.
                data = {**kept, "events": list(kept["events"])}
                if data["state"] == "started" and not _alive(data):
                    data["state"] = "stopped"
                    data["ended"] = time.time()
                    data["events"].append({
                        "type": "workflow_run", "id": data["id"], "run": data["run"],
                        "state": "stopped", "error": "The process running this workflow ended.",
                    })
                result.append(data)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return sorted(result, key=lambda data: data["started"])


def stop(workflow_id: str, run_id: str = "") -> bool:
    found = False
    for data in runs(workflow_id):
        if data["state"] == "started" and (not run_id or data["run"] == run_id):
            _path(workflow_id, data["run"], ".stop").touch(exist_ok=True)
            found = True
    return found


def cancelled(workflow_id: str, run_id: str) -> bool:
    return _path(workflow_id, run_id, ".stop").exists()


def running(workflow_id: str) -> bool:
    return any(data["state"] == "started" for data in runs(workflow_id))


def snapshot() -> list[dict]:
    return [event for data in runs() for event in data["events"]]
