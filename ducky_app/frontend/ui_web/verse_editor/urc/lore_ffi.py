"""ctypes bindings for lore_file_unstage / lore_file_reset / lore_repository_status.

Extracts ``lorelib`` from the user's URC.vsix (never vendored). Never CheckIn.
LoreString length is UTF-8 bytes without a NUL — an extra NUL is ``Repository not found``.
"""

from __future__ import annotations

import ctypes
import zipfile
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    c_int32,
    c_uint8,
    c_uint32,
    c_uint64,
    c_void_p,
    sizeof,
)
from pathlib import Path
from typing import Any

_DLL_NAME = "lorelib-amd64-unknown-windows.dll"
_VSIX_REL = Path("Epic Games") / "Fortnite" / "VSCode" / "URC.vsix"
_VSIX_MEMBER = f"extension/node_modules/@lore-vcs/sdk-amd64-unknown-windows/{_DLL_NAME}"

CALLBACK = CFUNCTYPE(None, c_void_p, c_uint64)

_TAG_ERROR = 1
_TAG_COMPLETE = 2
_TAG_END = 5
_TAG_STATUS_REV = 151
_TAG_STATUS_FILE = 152
# LoreRepositoryStatusRevisionEventData: two 16-byte ids + LoreString + hashes…
_STATUS_REV_LOCAL_AHEAD_OFF = 264

_ACTION_ADD = 1


class LoreString(Structure):
    _fields_ = [("string", ctypes.c_void_p), ("length", ctypes.c_size_t)]


class LoreStringArray(Structure):
    _fields_ = [("ptr", POINTER(LoreString)), ("count", ctypes.c_size_t)]


class LoreGlobalArgs(Structure):
    _fields_ = [
        ("repositoryPath", LoreString),
        ("correlationId", LoreString),
        ("identity", LoreString),
        ("force", c_uint8),
        ("offline", c_uint8),
        ("local", c_uint8),
        ("remote", c_uint8),
        ("dryRun", c_uint8),
        ("noAtime", c_uint8),
        ("maxConnections", c_uint32),
        ("searchLimit", c_uint32),
        ("searchNearest", c_uint8),
        ("noGc", c_uint8),
        ("inMemory", c_uint8),
        ("fileCountLimit", c_uint64),
        ("fileSizeLimit", c_uint64),
        ("compressTaskLimit", c_uint64),
        ("storeKeepAlive", c_uint8),
        ("storeKeepAliveSeconds", c_uint64),
        ("syncData", c_uint8),
        ("cache", c_uint8),
    ]


class LoreFileUnstageArgs(Structure):
    _fields_ = [("paths", LoreStringArray)]


class LoreFileResetArgs(Structure):
    _fields_ = [
        ("paths", LoreStringArray),
        ("revision", LoreString),
        ("purge", c_uint8),
    ]


class LoreRepositoryStatusArgs(Structure):
    _fields_ = [
        ("staged", c_uint8),
        ("scan", c_uint8),
        ("checkDirty", c_uint8),
        ("reset", c_uint8),
        ("syncPoint", c_uint8),
        ("revisionOnly", c_uint8),
        ("count", c_uint8),
        ("paths", LoreStringArray),
    ]


class LoreRevisionRevertArgs(Structure):
    _fields_ = [
        ("revision", LoreString),
        ("message", LoreString),
        ("noCommit", c_uint8),
    ]


class LoreEventCallback(Structure):
    _fields_ = [("userContext", c_uint64), ("callback", CALLBACK)]


assert sizeof(LoreString) == 16
assert sizeof(LoreStringArray) == 16
assert sizeof(LoreGlobalArgs) == 120
assert sizeof(LoreFileUnstageArgs) == 16
assert sizeof(LoreFileResetArgs) == 40
assert sizeof(LoreRepositoryStatusArgs) == 24
assert sizeof(LoreRevisionRevertArgs) == 40
assert sizeof(LoreEventCallback) == 16


def find_urc_vsix() -> Path | None:
    import os

    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(key)
        if base:
            p = Path(base) / _VSIX_REL
            if p.is_file():
                return p
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        p = Path(f"{letter}:/Program Files") / _VSIX_REL
        if p.is_file():
            return p
    return None


def extracted_dll_path() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True) / "lorelib" / "lorelib.dll"


def ensure_lorelib() -> Path:
    dest = extracted_dll_path()
    if dest.is_file() and dest.stat().st_size > 1_000_000:
        return dest
    vsix = find_urc_vsix()
    if vsix is None:
        raise FileNotFoundError("URC.vsix not found — install UEFN")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(vsix) as zf:
        dest.write_bytes(zf.read(_VSIX_MEMBER))
    return dest


def encode_lore_string(text: str) -> tuple[LoreString, Any]:
    """UTF-8 bytes only — do not put a NUL in ``length`` (lorelib then misses ``.lore``)."""
    encoded = (text or "").encode("utf-8")
    buf = ctypes.create_string_buffer(encoded)
    return LoreString(ctypes.addressof(buf), len(encoded)), buf


def _read_lore_str(data: int, offset: int) -> str:
    ptr = c_void_p.from_address(data + offset).value
    n = ctypes.c_size_t.from_address(data + offset + 8).value
    if not ptr or n <= 0:
        return ""
    return ctypes.string_at(ptr, int(n)).decode("utf-8", "replace").split("\0", 1)[0]


def _parse_event(ev: int) -> dict[str, Any]:
    tag = c_uint32.from_address(ev).value
    data = ev + 8
    out: dict[str, Any] = {"tag": tag}
    if tag == _TAG_ERROR:
        out["errorType"] = c_uint32.from_address(data).value
        out["errorInner"] = _read_lore_str(data, 8)
    elif tag == _TAG_COMPLETE:
        out["status"] = c_int32.from_address(data).value
        out["errorCode"] = c_int32.from_address(data + 8).value
        out["message"] = _read_lore_str(data, 16)
    elif tag == _TAG_STATUS_FILE:
        out["path"] = _read_lore_str(data, 0)
        out["action"] = c_uint32.from_address(data + 24).value
        out["flagStaged"] = c_uint8.from_address(data + 32).value
        out["flagDirty"] = c_uint8.from_address(data + 39).value
    elif tag == _TAG_STATUS_REV:
        out["isLocalAhead"] = bool(c_uint8.from_address(data + _STATUS_REV_LOCAL_AHEAD_OFF).value)
        out["branchName"] = _read_lore_str(data, 32)
    return out


class LoreFfiBackend:
    def __init__(self) -> None:
        self._lib: ctypes.CDLL | None = None
        self._events: list[dict[str, Any]] = []
        self._cb = CALLBACK(self._on_event)

    def _on_event(self, ev: int, _ctx: int) -> None:
        if ev:
            try:
                self._events.append(_parse_event(ev))
            except Exception:
                self._events.append({"tag": -1})

    def _load(self) -> ctypes.CDLL:
        if self._lib is not None:
            return self._lib
        lib = ctypes.CDLL(str(ensure_lorelib()))
        for name in (
            "lore_file_unstage",
            "lore_file_reset",
            "lore_repository_status",
            "lore_revision_revert",
        ):
            fn = getattr(lib, name)
            fn.restype = c_int32
        lib.lore_file_unstage.argtypes = [
            POINTER(LoreGlobalArgs),
            POINTER(LoreFileUnstageArgs),
            LoreEventCallback,
        ]
        lib.lore_file_reset.argtypes = [
            POINTER(LoreGlobalArgs),
            POINTER(LoreFileResetArgs),
            LoreEventCallback,
        ]
        lib.lore_repository_status.argtypes = [
            POINTER(LoreGlobalArgs),
            POINTER(LoreRepositoryStatusArgs),
            LoreEventCallback,
        ]
        lib.lore_revision_revert.argtypes = [
            POINTER(LoreGlobalArgs),
            POINTER(LoreRevisionRevertArgs),
            LoreEventCallback,
        ]
        self._lib = lib
        return lib

    def _globals(self, project_root: str, *, offline: int = 0) -> tuple[LoreGlobalArgs, Any]:
        # Official URC unstage/reset pass repositoryPath only.
        ls, buf = encode_lore_string(str(Path(project_root)))
        g = LoreGlobalArgs()
        g.repositoryPath = ls
        g.offline = offline
        return g, buf

    def _path_array(self, paths: list[str]) -> tuple[LoreStringArray, list[Any]]:
        keep: list[Any] = []
        items = (LoreString * len(paths))()
        for i, path in enumerate(paths):
            ls, buf = encode_lore_string(path.replace("\\", "/"))
            keep.append(buf)
            items[i] = ls
        keep.append(items)
        return LoreStringArray(items, len(paths)), keep

    def _cb_struct(self) -> LoreEventCallback:
        self._events = []
        return LoreEventCallback(0, self._cb)

    def _complete(self, rc: int) -> dict[str, Any]:
        msg = ""
        for ev in self._events:
            if ev.get("message"):
                msg = str(ev["message"])
            elif ev.get("errorInner"):
                msg = str(ev["errorInner"])
        return {"rc": int(rc), "ok": rc == 0, "message": msg, "events": list(self._events)}

    def drop(self, project_root: str, paths: list[str]) -> dict[str, Any]:
        lib = self._load()
        g, gbuf = self._globals(project_root)
        arr, keep = self._path_array(paths)
        cb = self._cb_struct()
        un = LoreFileUnstageArgs(arr)
        rc_u = lib.lore_file_unstage(ctypes.byref(g), ctypes.byref(un), cb)
        un_done = self._complete(rc_u)
        cb = self._cb_struct()
        rst = LoreFileResetArgs(arr, LoreString(), 1)
        rc_r = lib.lore_file_reset(ctypes.byref(g), ctypes.byref(rst), cb)
        rst_done = self._complete(rc_r)
        _ = (gbuf, keep)
        ok = rc_u == 0 or rc_r == 0
        return {
            "ok": bool(ok),
            "dropped": list(paths),
            "unstage_rc": int(rc_u),
            "reset_rc": int(rc_r),
            "unstage_message": un_done.get("message") or "",
            "reset_message": rst_done.get("message") or "",
        }

    def status(self, project_root: str) -> dict[str, Any]:
        lib = self._load()
        last: dict[str, Any] = {"ok": False, "status_rc": -1}
        for offline in (1, 0):
            g, gbuf = self._globals(project_root, offline=offline)
            args = LoreRepositoryStatusArgs()
            args.staged = 1
            args.scan = 1
            args.checkDirty = 1
            args.syncPoint = 1
            cb = self._cb_struct()
            rc = lib.lore_repository_status(ctypes.byref(g), ctypes.byref(args), cb)
            _ = gbuf
            last = self._status_from_events(rc)
            if rc == 0:
                return last
        return last

    def revert_local(self, project_root: str) -> dict[str, Any]:
        """Drop a local (unpushed) commit. ``noCommit`` — never CheckIn."""
        lib = self._load()
        g, gbuf = self._globals(project_root)
        args = LoreRevisionRevertArgs()
        args.noCommit = 1
        cb = self._cb_struct()
        rc = lib.lore_revision_revert(ctypes.byref(g), ctypes.byref(args), cb)
        _ = gbuf
        out = self._complete(rc)
        out["ok"] = rc == 0
        return out

    def _status_from_events(self, rc: int) -> dict[str, Any]:
        paths: list[str] = []
        staged: list[str] = []
        untracked: list[str] = []
        local_ahead = False
        branch = ""
        for ev in self._events:
            if ev.get("tag") == _TAG_STATUS_REV:
                local_ahead = bool(ev.get("isLocalAhead"))
                branch = str(ev.get("branchName") or "")
            if ev.get("tag") != _TAG_STATUS_FILE:
                continue
            rel = str(ev.get("path") or "").replace("\\", "/").lstrip("/")
            if not rel:
                continue
            paths.append(rel)
            if ev.get("flagStaged") or ev.get("flagDirty"):
                staged.append(rel)
            if ev.get("action") == _ACTION_ADD:
                untracked.append(rel)
        done = self._complete(rc)
        return {
            "ok": rc == 0,
            "status_rc": int(rc),
            "message": done.get("message") or "",
            "paths": paths,
            "staged": staged,
            "untracked": untracked,
            "is_local_ahead": local_ahead,
            "branch": branch,
        }
