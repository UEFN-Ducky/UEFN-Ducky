"""Read the one line that matters out of a Windows crash dump (.dmp).

WebView2 already writes Crashpad dumps for its own processes
(``%LOCALAPPDATA%/UEFN-Ducky/webview/EBWebView/Crashpad/reports``) and the app writes
one for itself (``frontend/native_crash.py``). Crash reports name what crashed from
them: the exception, the module it hit, and which process it was. The dump itself is
never sent anywhere; module names are cut to their file name, so no user path leaks.

Standard library only (runs from boot_report before the rest of Ducky loads).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

_HEADER = struct.Struct("<4sIIIIIQ")  # MINIDUMP_HEADER
_DIR_ENTRY = struct.Struct("<III")  # MINIDUMP_DIRECTORY: type, size, rva
_MODULE_SIZE = 108  # MINIDUMP_MODULE
_MODULE_LIST = 4
_EXCEPTION = 6
_CRASHPAD_INFO = 0x43500001

CODE_NAMES = {
    0xE0000008: "OUT_OF_MEMORY",
    0xC0000005: "ACCESS_VIOLATION",
    0xC0000409: "FAIL_FAST",
    0x80000003: "BREAKPOINT",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000017: "NO_MEMORY",
}
_PROCESS_NAMES = {
    "browser": "WebView2 browser process",
    "renderer": "WebView2 page process",
    "gpu-process": "WebView2 GPU process",
    "utility": "WebView2 utility process",
    "crashpad-handler": "WebView2 crash handler",
}
KEEP_DUMPS = 3


class _Bad(Exception):
    pass


def _u32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise _Bad("truncated")
    return struct.unpack_from("<I", data, at)[0]


def _u64(data: bytes, at: int) -> int:
    if at < 0 or at + 8 > len(data):
        raise _Bad("truncated")
    return struct.unpack_from("<Q", data, at)[0]


def _utf16(data: bytes, rva: int) -> str:
    """MINIDUMP_STRING: byte length, then UTF-16LE."""
    size = _u32(data, rva)
    if rva + 4 + size > len(data):
        raise _Bad("truncated")
    return data[rva + 4 : rva + 4 + size].decode("utf-16-le", errors="replace")


def _utf8(data: bytes, rva: int) -> str:
    """Crashpad MinidumpUTF8String / MinidumpByteArray: length, then bytes."""
    size = _u32(data, rva)
    if rva + 4 + size > len(data):
        raise _Bad("truncated")
    return data[rva + 4 : rva + 4 + size].decode("utf-8", errors="replace").rstrip("\0")


def _base_name(path: str) -> str:
    return path.replace("/", "\\").rsplit("\\", 1)[-1]


def _annotations(data: bytes, rva: int) -> dict[str, str]:
    """Crashpad's simple annotations and per-module annotation objects (``ptype`` lives
    in one of them, depending on the Crashpad version)."""
    out: dict[str, str] = {}
    # MinidumpCrashpadInfo: version, report_id[16], client_id[16], simple_annotations
    # (size, rva), module_list (size, rva).
    simple_rva = _u32(data, rva + 4 + 32 + 4)
    if _u32(data, rva + 4 + 32):
        for i in range(min(_u32(data, simple_rva), 256)):
            key, value = _u32(data, simple_rva + 4 + 8 * i), _u32(data, simple_rva + 8 + 8 * i)
            out[_utf8(data, key)] = _utf8(data, value)
    if not _u32(data, rva + 4 + 32 + 8):
        return out
    modules_rva = _u32(data, rva + 4 + 32 + 12)
    for i in range(min(_u32(data, modules_rva), 512)):
        # MinidumpModuleCrashpadInfoLink: module index, location (size, rva).
        info_rva = _u32(data, modules_rva + 4 + 12 * i + 8)
        # MinidumpModuleCrashpadInfo: version, list_annotations, simple_annotations, annotation_objects.
        if _u32(data, info_rva + 12):
            simple = _u32(data, info_rva + 16)
            for j in range(min(_u32(data, simple), 256)):
                out.setdefault(_utf8(data, _u32(data, simple + 4 + 8 * j)), _utf8(data, _u32(data, simple + 8 + 8 * j)))
        if _u32(data, info_rva + 20):
            objects = _u32(data, info_rva + 24)
            for j in range(min(_u32(data, objects), 512)):
                # MinidumpAnnotation: name rva, type u16, reserved u16, value rva.
                entry = objects + 4 + 12 * j
                try:
                    out.setdefault(_utf8(data, _u32(data, entry)), _utf8(data, _u32(data, entry + 8)))
                except _Bad:
                    continue
    return out


def parse(data: bytes) -> dict[str, Any]:
    """``{code, code_name, address, module, offset, main_module, process_type, params}``,
    or ``{unreadable: True, error}`` for anything that isn't a whole minidump."""
    try:
        if len(data) < _HEADER.size:
            raise _Bad("truncated")
        sig, _version, streams, dir_rva, _sum, _stamp, _flags = _HEADER.unpack_from(data, 0)
        if sig != b"MDMP":
            raise _Bad("not a minidump")
        if dir_rva + streams * _DIR_ENTRY.size > len(data):
            raise _Bad("truncated")
        found = {}
        for i in range(streams):
            kind, size, rva = _DIR_ENTRY.unpack_from(data, dir_rva + i * _DIR_ENTRY.size)
            found.setdefault(kind, (size, rva))
        modules: list[tuple[int, int, str]] = []
        if _MODULE_LIST in found:
            rva = found[_MODULE_LIST][1]
            for i in range(min(_u32(data, rva), 4096)):
                at = rva + 4 + i * _MODULE_SIZE
                modules.append((_u64(data, at), _u32(data, at + 8), _base_name(_utf16(data, _u32(data, at + 20)))))
        out: dict[str, Any] = {
            "code": None, "code_name": "", "address": None, "module": "", "offset": None,
            "main_module": modules[0][2] if modules else "", "process_type": "", "params": [],
        }
        if _EXCEPTION in found:
            rva = found[_EXCEPTION][1]
            # MINIDUMP_EXCEPTION_STREAM: thread id, align, then MINIDUMP_EXCEPTION.
            code = _u32(data, rva + 8)
            address = _u64(data, rva + 24)
            count = min(_u32(data, rva + 32), 15)
            out.update(
                code=code,
                code_name=CODE_NAMES.get(code, f"0x{code:08X}"),
                address=address,
                params=[_u64(data, rva + 40 + 8 * i) for i in range(count)],
            )
            for base, size, name in modules:
                if base <= address < base + size:
                    out.update(module=name, offset=address - base)
                    break
        if _CRASHPAD_INFO in found:
            try:
                out["process_type"] = _annotations(data, found[_CRASHPAD_INFO][1]).get("ptype", "")
            except _Bad:
                pass
        return out
    except (_Bad, struct.error) as exc:
        return {"unreadable": True, "error": str(exc) or "unreadable"}


def read_signal(data: bytes) -> dict[str, Any]:
    """A Linux ``.sigcrash`` (native_crash.write_signal_report) in parse()'s shape."""
    import json

    try:
        raw = json.loads(data.decode("utf-8"))
        return {
            "code": int(raw["code"]), "code_name": str(raw["signal"]), "address": None,
            "module": str(raw.get("module") or ""), "offset": None,
            "main_module": str(raw.get("process") or ""), "process_type": "", "params": [],
        }
    except (ValueError, KeyError, TypeError) as exc:
        return {"unreadable": True, "error": str(exc) or "unreadable"}


def read(path: Path) -> dict[str, Any]:
    try:
        if Path(path).suffix == ".sigcrash":
            return read_signal(Path(path).read_bytes())
        return parse(Path(path).read_bytes())
    except OSError as exc:
        return {"unreadable": True, "error": exc.strerror or "unreadable"}


def process_label(info: dict[str, Any]) -> str:
    ptype = str(info.get("process_type") or "")
    if ptype:
        return _PROCESS_NAMES.get(ptype, f"WebView2 {ptype} process")
    main = str(info.get("main_module") or "").lower()
    if main == "msedgewebview2.exe":
        return "WebView2 process"
    return f"{info.get('main_module')} process" if main else "a process"


def where(info: dict[str, Any]) -> str:
    if info.get("module"):
        if info.get("offset") is None:
            return str(info["module"])  # a Linux signal report: no address
        return f"{info['module']}+0x{int(info.get('offset') or 0):x}"
    return f"0x{int(info['address']):x}" if info.get("address") is not None else "an unknown place"


def summary(info: dict[str, Any]) -> str:
    """e.g. "WebView2 browser process: OUT_OF_MEMORY at KERNELBASE.dll+0xc483a (2 MB allocation failed)"."""
    if info.get("unreadable"):
        return f"unreadable dump ({info.get('error') or 'unreadable'})"
    if info.get("code") is None:
        return f"{process_label(info)}: no exception recorded"
    text = f"{process_label(info)}: {info['code_name']} at {where(info)}"
    params = info.get("params") or []
    if info["code"] == 0xE0000008 and params and params[0]:
        # Chromium's OOM exception passes the failed allocation size first.
        size = params[0]
        text += f" ({size / 1024**2:.0f} MB allocation failed)" if size >= 1024**2 else f" ({size} bytes allocation failed)"
    elif info["code"] == 0xC0000005 and len(params) >= 2:
        verb = {0: "reading", 1: "writing", 8: "running"}.get(params[0], "touching")
        text += f" ({verb} 0x{params[1]:x})"
    return text


def cause(info: dict[str, Any], *, app: bool = False) -> str:
    """One plain sentence for the crash pop-up."""
    if info.get("unreadable") or info.get("code") is None:
        return ""
    who, tail = ("Ducky's app process", "") if app else ("The chat window", f" ({process_label(info)})")
    if info["code"] in (0xE0000008, 0xC0000017):
        return f"{who} ran out of memory{tail}."
    return f"{who} crashed{tail}: {info['code_name']} in {where(info)}."


def dumps_since(folder: Path, since: float, pattern: str = "*.dmp") -> list[Path]:
    """Dumps in *folder* written at or after *since*, oldest first."""
    try:
        files = [p for p in Path(folder).glob(pattern) if p.is_file() and p.stat().st_mtime >= since]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime)


def prune(folder: Path, keep: int = KEEP_DUMPS, pattern: str = "*.dmp") -> None:
    """Keep the *keep* newest dumps in *folder*; delete the rest."""
    try:
        files = sorted((p for p in Path(folder).glob(pattern) if p.is_file()), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        print(f"{Path(arg).name}: {summary(read(Path(arg)))}")
