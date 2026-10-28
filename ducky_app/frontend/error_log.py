"""Rolling 24-hour panel logs.

Stored as JSON lines under ``%LOCALAPPDATA%/UEFN-Ducky/``.
``errors.jsonl`` = failures; ``activity.jsonl`` = session/app lines.
Both are trimmed on every write and read so only the last 24 hours remain.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from frontend.settings import default_app_data_dir

MAX_ENTRIES = 2000
MAX_AGE_S = 24 * 3600
_ERRORS_NAME = "errors.jsonl"
_ACTIVITY_NAME = "activity.jsonl"


def errors_path() -> Path:
    return default_app_data_dir() / _ERRORS_NAME


def activity_path() -> Path:
    return default_app_data_dir() / _ACTIVITY_NAME


def record_error(source: str, message: str, *, count: bool = True, at: str = "", kind: str = "") -> None:
    """Append one error. Skips if identical to the most recent entry (dedupe spam).

    ``count=False``: the anonymous usage counts already have it some other way (a failed
    tool call is a ``tool.call`` with ok=no), so it is not an ``error.code`` as well."""
    _append(_ERRORS_NAME, source, message, dedupe=True, count=count, at=at, kind=kind)


def record_activity(source: str, message: str) -> None:
    """Append one activity line (panel / plugin log)."""
    _append(_ACTIVITY_NAME, source, message, dedupe=False, count=False)


def read_errors(limit: int = MAX_ENTRIES) -> list[dict]:
    return _read(_ERRORS_NAME, limit)


def read_activity(limit: int = MAX_ENTRIES) -> list[dict]:
    return _read(_ACTIVITY_NAME, limit)


def counts() -> dict[str, int]:
    """How many error and activity lines there are, without reading them: the Feedback
    tab only shows the numbers, and reading scrubs every row (~1 s for 2000 long ones)."""
    out: dict[str, int] = {}
    for key, name in (("errors", _ERRORS_NAME), ("log", _ACTIVITY_NAME)):
        if _use_db():
            try:
                _ingest_listener_file(name)
                repo = _repo()
                repo.trim(_KIND[name], older_than=time.time() - MAX_AGE_S, keep=MAX_ENTRIES)
                out[key] = repo.count(_KIND[name])
            except Exception:
                out[key] = 0
            continue
        _trim_file(name)
        try:
            out[key] = sum(1 for ln in _path(name).read_text(encoding="utf-8").splitlines() if ln.strip())
        except OSError:
            out[key] = 0
    return out


def clear_errors() -> None:
    _clear(_ERRORS_NAME)


def clear_activity() -> None:
    _clear(_ACTIVITY_NAME)


def trim() -> None:
    """Drop anything older than 24 hours. Safe to call anytime."""
    _trim_file(_ERRORS_NAME)
    _trim_file(_ACTIVITY_NAME)


def _scrub(text: str) -> str:
    """Personal data out before a line is stored or shown (backend.util.privacy.scrub):
    the home folder becomes ``~``; emails, keys, tokens, passwords, signed links and
    network addresses are removed."""
    from backend.util.privacy import scrub

    return scrub(text)


_crash_hooks_on = False


def _crash_at(tb) -> str:
    """The innermost Ducky frame (``backend.``/``frontend.``) as a ``module.function`` slug."""
    at = ""
    while tb is not None:
        mod = str(tb.tb_frame.f_globals.get("__name__") or "")
        if mod.startswith(("backend.", "frontend.")):
            at = f"{mod}.{tb.tb_frame.f_code.co_name}"
        tb = tb.tb_next
    return re.sub(r"[^a-z0-9_.-]+", "_", at.lower()).strip("_.-")[:64]


def install_crash_hooks() -> None:
    """Uncaught exceptions (main thread and other threads) land in Errors as ``py.<Type>``,
    then the previous hook runs. Installs once."""
    global _crash_hooks_on
    if _crash_hooks_on:
        return
    _crash_hooks_on = True
    import sys
    import threading
    import traceback

    from frontend import usage_events as ev

    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def report(exc_type, exc, tb) -> None:
        if exc_type is None or issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
            return
        try:
            # The kind from the exception line only: file paths in the traceback could match words.
            kind = ev.kind_of(f"{exc_type.__name__}: {exc}")
            record_error("py." + exc_type.__name__, "".join(traceback.format_exception(exc_type, exc, tb)),
                         at=_crash_at(tb), kind="crash" if kind == "other" else kind)
        except Exception:
            pass

    def sys_hook(exc_type, exc, tb) -> None:
        report(exc_type, exc, tb)
        prev_sys(exc_type, exc, tb)

    def thread_hook(args) -> None:
        report(args.exc_type, args.exc_value, args.exc_traceback)
        prev_thread(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def format_entries(entries: list[dict]) -> list[str]:
    """Newest-first lines for the Settings console."""
    lines: list[str] = []
    for e in entries:
        ts = e.get("ts", 0)
        try:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
        except (ValueError, TypeError):
            stamp = "?"
        lines.append(f"[{stamp}] ({e.get('source', '?')}) {e.get('message', '')}")
    return lines


def _path(name: str) -> Path:
    return default_app_data_dir() / name


_KIND = {_ERRORS_NAME: "error", _ACTIVITY_NAME: "activity"}


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("events")


def _repo():
    from backend.store.importers import phase4
    from backend.store.repos import events as repo

    phase4.ensure("logs")
    return repo


def _ingest_listener_file(name: str) -> None:
    """The UEFN listener (Epic's Python, no database) still appends to
    errors.jsonl; fold those lines into rows and truncate the file."""
    repo = _repo()  # runs the one-time legacy import first (it moves the old file)
    path = _path(name)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("message"):
            rows.append({"ts": row.get("ts") or time.time(), "source": row.get("source"), "message": _scrub(str(row.get("message") or ""))})
    try:
        if rows:
            repo.insert_many(_KIND[name], rows)
        path.write_text("", encoding="utf-8")
    except OSError:
        pass


def _append(name: str, source: str, message: str, *, dedupe: bool, count: bool, at: str = "", kind: str = "") -> None:
    message = _scrub((message or "").strip())
    if not message:
        return
    if name == _ERRORS_NAME and count:
        try:
            from frontend import usage_stats

            usage_stats.error(source or "", message, at, kind)
        except Exception:
            pass
    if _use_db():
        try:
            repo = _repo()
            kind = _KIND[name]
            if dedupe and repo.last_message(kind) == message:
                return
            repo.insert(kind, ts=time.time(), source=(source or "?")[:64], message=message[:2000])
            repo.trim(kind, older_than=time.time() - MAX_AGE_S, keep=MAX_ENTRIES)
        except Exception:
            pass
        return
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if dedupe and _last_message(path) == message:
            return
        entry = {"ts": time.time(), "source": (source or "?")[:64], "message": message[:2000]}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        _trim_file(name)
    except OSError:
        pass


def _read(name: str, limit: int) -> list[dict]:
    if _use_db():
        try:
            _ingest_listener_file(name)
            repo = _repo()
            kind = _KIND[name]
            repo.trim(kind, older_than=time.time() - MAX_AGE_S, keep=MAX_ENTRIES)
            return [
                {"ts": r["ts"], "source": r["source"], "message": _scrub(str(r["message"] or ""))}
                for r in repo.newest(kind, limit=max(1, int(limit)))
            ]
        except Exception:
            return []
    _trim_file(name)
    path = _path(name)
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                row = dict(row)
                row["message"] = _scrub(str(row.get("message") or ""))
                out.append(row)
    except OSError:
        return []
    out.reverse()
    return out[: max(1, int(limit))]


def _clear(name: str) -> None:
    if _use_db():
        try:
            _repo().clear(_KIND[name])
        except Exception:
            pass
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    except OSError:
        pass


def _last_message(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return None
        return json.loads(lines[-1]).get("message")
    except (OSError, ValueError):
        return None


def _trim_file(name: str) -> None:
    if _use_db():
        return
    path = _path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    cutoff = time.time() - MAX_AGE_S
    kept: list[str] = []
    for ln in lines[-MAX_ENTRIES:]:
        try:
            if float(json.loads(ln).get("ts", 0)) >= cutoff:
                kept.append(ln)
        except (ValueError, TypeError):
            continue
    if kept == lines:
        return
    try:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError:
        pass
