"""Reclaim PyInstaller extraction directories left behind by killed processes.

The app ships as a one-file EXE, so every launch extracts the whole bundle
(~120 MB) into ``%TEMP%/_MEI<random>`` and the bootloader removes it again on
a clean exit. A process that is *killed* never gets that far, and the folder
stays forever.

That is routine for the MCP bridge: an IDE starts ``UEFN-Ducky.exe bridge`` as
a stdio server and shuts it down by killing it, then respawns on every restart,
config reload, and reconnect. Measured on one developer machine: 113 leftover
folders, 12.9 GB, in about 24 hours.

Deleting these is only safe if nothing is using them, and "in use" cannot be
inferred from age — a bridge can live for days. Windows keeps the interpreter
DLL mapped for the life of a process, so a *write* open of it fails exactly
when some process still has the directory live. That probe, not a timestamp,
is what decides. The age floor only protects a directory that is still being
extracted, where the DLL is not on disk yet.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

__all__ = ["sweep_stale_extract_dirs", "sweep_stale_temp_dirs", "start_background_sweep"]

PREFIX = "_MEI"
#: A directory younger than this may still be mid-extraction, where the
#: interpreter DLL does not exist yet and the lock probe cannot see it.
MIN_AGE_S = 300.0
#: Stop after this long; whatever is left is picked up by the next launch.
#: Deleting one ~120 MB directory takes roughly a second, so this clears about
#: 60 per launch — a user updating with a year of backlog is clean within a
#: couple of starts, and the MCP bridge starts often. Runs on a daemon thread,
#: so a slow disk delays nothing the user is waiting for.
DEFAULT_BUDGET_S = 60.0
#: Support escape hatch.
DISABLE_ENV = "UEFN_DUCKY_NO_TEMP_SWEEP"


def _own_extract_dir() -> str:
    """This process's own extraction directory, normalized. Never sweep it."""
    raw = getattr(sys, "_MEIPASS", None)
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.abspath(raw))
    except Exception:
        return ""


def _lock_probe_target(entry: Path) -> Path | None:
    """The file a live process keeps mapped, or None if the dir looks inert.

    The interpreter DLL is held for the life of the process. Extension modules
    are the fallback for layouts that name the DLL differently.
    """
    try:
        for dll in sorted(entry.glob("python*.dll")):
            if dll.is_file():
                return dll
        for pyd in sorted(entry.glob("*.pyd")):
            if pyd.is_file():
                return pyd
    except OSError:
        # Unreadable directory: treat as in use rather than risk deleting it.
        return entry
    return None


def _dir_in_use(entry: Path) -> bool:
    """True when some process still has this extraction directory live.

    A directory with nothing lockable left in it is a partially removed
    leftover, which is safe to finish removing.
    """
    target = _lock_probe_target(entry)
    if target is None:
        return False
    if target == entry:
        return True
    try:
        # Opening for write fails while the image is mapped by a live process.
        with open(target, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        # Gone or unreadable between the glob and the open — nothing to protect.
        return False


def sweep_stale_extract_dirs(
    temp_dir: str | os.PathLike[str] | None = None,
    *,
    min_age_s: float = MIN_AGE_S,
    budget_s: float = DEFAULT_BUDGET_S,
    now: float | None = None,
) -> tuple[int, int]:
    """Delete extraction directories no live process is using.

    Returns ``(directories_removed, bytes_reclaimed)``. Never raises: a failed
    sweep must never stop the app from starting.
    """
    if os.environ.get(DISABLE_ENV, "").strip():
        return (0, 0)
    if budget_s <= 0:
        return (0, 0)
    root = Path(temp_dir) if temp_dir is not None else Path(_default_temp())
    own = _own_extract_dir()
    deadline = time.monotonic() + max(0.0, budget_s)
    clock = time.time() if now is None else now
    removed = 0
    freed = 0
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return (0, 0)
    for entry in entries:
        if time.monotonic() >= deadline:
            break
        try:
            if not entry.name.startswith(PREFIX) or not entry.is_dir():
                continue
            if own and os.path.normcase(os.path.abspath(str(entry))) == own:
                continue
            if clock - entry.stat().st_mtime < min_age_s:
                continue
            if _dir_in_use(entry):
                continue
            size = _dir_size(entry)
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed += 1
                freed += size
        except OSError:
            continue
    return (removed, freed)


def _default_temp() -> str:
    import tempfile

    return tempfile.gettempdir()


def _dir_size(entry: Path) -> int:
    total = 0
    try:
        for path in entry.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def sweep_stale_temp_dirs(
    prefix: str,
    *,
    temp_dir: str | os.PathLike[str] | None = None,
    min_age_s: float = 7200.0,
    budget_s: float = 10.0,
    now: float | None = None,
) -> tuple[int, int]:
    """Delete our own scratch directories left behind by crashed runs.

    For throwaway trees we create ourselves — pytest's isolated AppData, the
    end-to-end driver's browser profiles — where the only thing protecting a
    live one is age. A browser profile can be a third of a gigabyte, so a few
    crashed runs cost more disk than the app itself.

    Never raises, and only ever touches names starting with ``prefix``.
    """
    if os.environ.get(DISABLE_ENV, "").strip():
        return (0, 0)
    if budget_s <= 0 or not prefix:
        return (0, 0)
    root = Path(temp_dir) if temp_dir is not None else Path(_default_temp())
    deadline = time.monotonic() + budget_s
    clock = time.time() if now is None else now
    removed = 0
    freed = 0
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return (0, 0)
    for entry in entries:
        if time.monotonic() >= deadline:
            break
        try:
            if not entry.name.startswith(prefix) or not entry.is_dir():
                continue
            if clock - entry.stat().st_mtime < min_age_s:
                continue
            size = _dir_size(entry)
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed += 1
                freed += size
        except OSError:
            continue
    return (removed, freed)


def remove_tree_with_retry(path: str | os.PathLike[str], *, attempts: int = 5, delay_s: float = 0.4) -> bool:
    """Delete a tree, retrying while a just-killed process releases its handles.

    A browser told to exit still holds its profile for a moment; a single
    ``rmtree(ignore_errors=True)`` silently leaves most of it behind.
    """
    target = Path(path)
    for attempt in range(max(1, attempts)):
        if not target.exists():
            return True
        shutil.rmtree(target, ignore_errors=True)
        if not target.exists():
            return True
        if attempt + 1 < attempts:
            time.sleep(delay_s)
    return not target.exists()


def start_background_sweep(**kwargs: object) -> None:
    """Run the sweep on a daemon thread so startup never waits on disk I/O."""
    import threading

    def _run() -> None:
        try:
            sweep_stale_extract_dirs(**kwargs)  # type: ignore[arg-type]
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="ducky-temp-sweep").start()
