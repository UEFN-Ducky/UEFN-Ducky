"""The install folder's EXEs between runs: what changed while Ducky was off, and a broken
root launcher put back before anyone clicks it.

Nov 10 2026: after a normal PC restart, the Start menu, desktop and Run-key start of
``Programs\\UEFN Ducky\\Ducky-Launcher.exe`` said "This app can't run on your PC"; rerunning
Setup fixed it and erased the evidence. Two writers touch that file: Inno Setup and
:func:`frontend.app_versions.copy_launcher_files` (each version folder that reaches ready).

    %LOCALAPPDATA%\\UEFN-Ducky\\launcher\\
        install_health.json     the root EXEs' fingerprints at the last start/ready/exit
        install_health.log      one JSON line per change found at a start, and per repair
        good\\Ducky-Launcher.exe the last root launcher that was a whole Windows program

Stdlib only, never raises, never blocks a start for more than a file read.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from frontend.app_paths import APP_DATA_DIR_NAME, local_app_data_base

LAUNCHER = "Ducky-Launcher.exe"
STUB = "UEFN-Ducky.exe"
FILES = (LAUNCHER, STUB, "UEFN-Ducky-Bridge.exe")
STATE_FILE = "install_health.json"
LOG_FILE = "install_health.log"
HEAD_BYTES = 4096
LOG_MAX_BYTES = 256 * 1024
SETUP_LOGS_KEPT = 5
# Inno rewrites it on every Setup run, by hand or from the updater.
UNINSTALL_DATA = "unins000.dat"


def state_dir() -> Path:
    return local_app_data_base() / APP_DATA_DIR_NAME / "launcher"


def good_copy() -> Path:
    return state_dir() / "good" / LAUNCHER


def setup_logs_dir() -> Path:
    return local_app_data_base() / APP_DATA_DIR_NAME / "logs"


def install_root() -> Path | None:
    """The registered install folder of a packaged, non-sandboxed Ducky, else None."""
    # Test runs (conftest's isolated AppData) never read or repair the PC's real install.
    if not getattr(sys, "frozen", False) or any(os.environ.get(k) for k in (
            "DUCKY_SANDBOX", "UEFN_DUCKY_NO_VERSION_HOP", "DUCKY_TESTS_ISOLATED_APPDATA")):
        return None
    try:
        from frontend.install_info import installed_location

        location = installed_location()
    except Exception:
        return None
    root = Path(location) if location else None
    return root if root is not None and root.is_dir() else None


# --- is it a whole Windows program? ------------------------------------------------


def starts_with_mz(path: Path) -> bool:
    """The quick check (native_launcher repoint.rs ``looks_like_exe``): a zero-filled EXE fails."""
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"MZ"
    except OSError:
        return False


def exe_ok(path: Path) -> bool:
    """``MZ``, a ``PE`` header, and every section's bytes inside the file: a zero-filled or
    cut-short EXE (Windows: "This app can't run on your PC") is False."""
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            head = fh.read(64 * 1024)
    except OSError:
        return False
    if len(head) < 64 or head[:2] != b"MZ":
        return False
    pe = int.from_bytes(head[60:64], "little")
    if pe + 24 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return False
    sections = int.from_bytes(head[pe + 6:pe + 8], "little")
    table = pe + 24 + int.from_bytes(head[pe + 20:pe + 22], "little")
    if sections == 0 or table + 40 * sections > len(head):
        return False
    end = 0
    for i in range(sections):
        row = table + 40 * i
        raw_size = int.from_bytes(head[row + 16:row + 20], "little")
        raw_at = int.from_bytes(head[row + 20:row + 24], "little")
        if raw_size:
            end = max(end, raw_at + raw_size)
    return 0 < end <= size


def fingerprint(path: Path) -> dict[str, Any] | None:
    """Size, mtime, the first 4 KB's sha256, whole-program check, zero-filled head; None
    when the file is not there."""
    try:
        st = path.stat()
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
    except OSError:
        return None
    return {
        "size": st.st_size,
        "mtime": round(st.st_mtime, 3),
        "head_sha": hashlib.sha256(head).hexdigest(),
        "ok": exe_ok(path),
        "zeros": bool(head) and not head.strip(b"\0"),
    }


def snapshot(root: Path) -> dict[str, Any]:
    return {name: fingerprint(root / name) for name in FILES}


def changes(before: dict[str, Any] | None, after: dict[str, Any]) -> list[dict[str, Any]]:
    """Files whose size, mtime or head differ (or that came or went) between two snapshots."""
    if not isinstance(before, dict):
        return []
    found = []
    for name in FILES:
        old, new = before.get(name), after.get(name)
        same = (old is None and new is None) or (
            isinstance(old, dict) and isinstance(new, dict)
            and all(old.get(k) == new.get(k) for k in ("size", "mtime", "head_sha")))
        if not same:
            found.append({"file": name, "before": old, "after": new})
    return found


# --- state, log, telemetry ----------------------------------------------------------


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())  # a restart right after must not leave zeros
    os.replace(tmp, path)


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads((state_dir() / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(root: Path, files: dict[str, Any], by: str) -> None:
    body = {"root": str(root), "files": files, "by": by, "at": time.time(), "pid": os.getpid()}
    _write_atomic(state_dir() / STATE_FILE, json.dumps(body).encode("utf-8"))


def _log(row: dict[str, Any]) -> None:
    path = state_dir() / LOG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **row}) + "\n")
    except OSError:
        pass


def _track(code: str, file: str, kind: str) -> None:
    try:
        from frontend import usage_stats

        usage_stats.track("error.code", code=code, where="other", kind=kind,
                          reason=file.lower().replace(".exe", "").replace("-", "_"),
                          version=usage_stats._app_version())
    except Exception:
        pass


def boot_time() -> float:
    """When Windows last started (0 when unknown)."""
    if sys.platform != "win32":
        return 0.0
    try:
        ticks = ctypes.windll.kernel32.GetTickCount64
        ticks.restype = ctypes.c_uint64
        return time.time() - ticks() / 1000.0
    except Exception:
        return 0.0


def setup_ran_since(root: Path, since: float) -> bool:
    """A Setup run (by hand or the updater) since ``since``: Inno rewrites unins000.dat, and the
    updater's runs leave ``setup-*.log``."""
    try:
        if (root / UNINSTALL_DATA).stat().st_mtime > since:
            return True
    except OSError:
        pass
    try:
        return any(p.stat().st_mtime > since for p in setup_logs_dir().glob("setup-*.log"))
    except OSError:
        return False


def setup_log_args() -> list[str]:
    """``/LOG=<file>`` for a Setup run, so every run leaves a log; keeps the newest few."""
    folder = setup_logs_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        logs = sorted(folder.glob("setup-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in logs[SETUP_LOGS_KEPT - 1:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return [f"/LOG={folder / time.strftime('setup-%Y%m%d-%H%M%S.log')}"]


# --- the good copy and the repair ---------------------------------------------------


def keep_good_copy(root: Path) -> bool:
    """Save the root launcher as the last good one when it is a whole program and differs."""
    src, dst = root / LAUNCHER, good_copy()
    try:
        if not exe_ok(src):
            return False
        data = src.read_bytes()
        if dst.is_file() and dst.stat().st_size == len(data) and dst.read_bytes() == data:
            return False
        _write_atomic(dst, data)
        return True
    except OSError:
        return False


def repair_launcher(root: Path) -> bool:
    """Put the last good launcher back over a root launcher that is not a whole program.
    A good copy that is not one itself is never used."""
    dst, good = root / LAUNCHER, good_copy()
    if not dst.exists() or exe_ok(dst) or not exe_ok(good):
        return False
    try:
        data = good.read_bytes()
        tmp = dst.with_name(f"{LAUNCHER}.{os.getpid()}.repair")
        _write_atomic(tmp, data)
        if tmp.read_bytes() != data:
            tmp.unlink(missing_ok=True)
            return False
        try:
            os.replace(tmp, dst)
        except PermissionError:  # running: Windows lets it be renamed aside
            os.replace(dst, dst.with_name(LAUNCHER + ".old"))
            os.replace(tmp, dst)
        return True
    except OSError:
        return False


# --- entry points ---------------------------------------------------------------------


def check_at_start(root: Path | None = None) -> dict[str, Any]:
    """Every Ducky start: log what changed in the install root since Ducky last looked, put a
    broken root launcher back, keep the good copy current. Returns what it found."""
    try:
        root = root or install_root()
        if root is None:
            return {}
        state = _read_state()
        files = snapshot(root)
        found = changes(state.get("files") if state.get("root") == str(root) else None, files)
        report: dict[str, Any] = {"changes": found}
        if found:
            since = float(state.get("at") or 0)
            report.update(last_seen=since, last_seen_by=state.get("by"), boot=boot_time(),
                          setup_ran=setup_ran_since(root, since))
            _log({"event": "changed_while_off", **report})
            for row in found:
                after = row["after"]
                broken = after is None or not after.get("ok")
                code = "install_exe_broken" if broken else "install_exe_changed"
                kind = "not_found" if after is None else "crash" if broken else "other"
                if broken or not report["setup_ran"]:
                    _track(code, row["file"], kind)
        if files.get(LAUNCHER) and not files[LAUNCHER].get("ok"):
            repaired = repair_launcher(root)
            report["repaired"] = repaired
            _log({"event": "launcher_repaired" if repaired else "launcher_broken_no_good_copy",
                  "launcher": files[LAUNCHER]})
            _track("install_launcher_repaired" if repaired else "install_launcher_unrepaired",
                   LAUNCHER, "crash")
            files = snapshot(root)
        keep_good_copy(root)
        _save_state(root, files, "start")
        return report
    except Exception:
        return {}


def record(by: str, root: Path | None = None) -> None:
    """The root EXEs as they are now (at ready, after Ducky replaced the launcher itself, and
    at a clean exit), so the next start reports only what changed while Ducky was off."""
    try:
        root = root or install_root()
        if root is None:
            return
        keep_good_copy(root)
        _save_state(root, snapshot(root), by)
    except Exception:
        pass


def stub_broken(root: Path | None = None) -> bool:
    root = root or install_root()
    return root is not None and (root / STUB).exists() and not exe_ok(root / STUB)


def after_ready() -> None:
    """The panel is up (notifications bound): record, and say so once when the install's
    UEFN-Ducky.exe itself is broken, which only Setup can put back."""
    record("ready")
    try:
        if stub_broken():
            _log({"event": "stub_broken"})
            _track("install_stub_broken", STUB, "crash")
            from frontend import notifications

            notifications.alert("UEFN Ducky", "Some installed files are damaged.", level="error",
                                detail="Run the latest UEFN Ducky Setup from uefnducky.org again to "
                                       "repair the installation. Your chats and settings are kept.")
    except Exception:
        pass
