"""Graphics memory (VRAM): how full the main GPU is, and which programs fill it.

Nov 5 2026: UEFN crashed with "Out of video memory" on an 8 GB card while Ducky watched only
system RAM. DXGI names the main adapter (its dedicated memory, the OS budget and its LUID);
IDXGIAdapter3::QueryVideoMemoryInfo only counts the calling process, so the PC-wide usage and
each program's share come from the GPU performance counters ("GPU Adapter Memory" and
"GPU Process Memory", Dedicated Usage) of that adapter. Any failure gives None, never an error.

Linux: NVIDIA through NVML (libnvidia-ml, with each program's share), AMD through the amdgpu
sysfs files (``/sys/class/drm/card*/device/mem_info_vram_{used,total}``, no per-program split).
Any other card is unknown (None), which never sets light drawing.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from typing import Any

FULL_SHARE = 0.90  # of the card's dedicated memory: a finding and one alert per spell
_GB = 1024**3
_UEFN = ("unrealeditorfortnite", "unrealeditor")


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        import uuid

        b = uuid.UUID(text).bytes_le
        g = cls()
        ctypes.memmove(ctypes.byref(g), b, 16)
        return g


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("LuidLow", ctypes.c_uint32), ("LuidHigh", ctypes.c_int32), ("Flags", ctypes.c_uint)]


class _VideoMemoryInfo(ctypes.Structure):
    _fields_ = [("Budget", ctypes.c_uint64), ("CurrentUsage", ctypes.c_uint64),
                ("AvailableForReservation", ctypes.c_uint64), ("CurrentReservation", ctypes.c_uint64)]


def _method(obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Any:
    """COM vtable slot ``index`` of ``obj`` as a callable returning HRESULT."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    fn = proto(vtable[index])
    return lambda *args: fn(obj, *args)


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _method(obj, 2)()


def _adapter() -> dict[str, Any] | None:
    """The main hardware adapter (most dedicated memory): name, LUID text, dedicated bytes and
    the OS budget (QueryVideoMemoryInfo, local segment)."""
    if sys.platform != "win32":
        return None
    dxgi = ctypes.WinDLL("dxgi")
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_GUID.parse("770aae78-f26f-4dba-a829-253c83d1b387")),
                               ctypes.byref(factory)) != 0:
        return None
    best: dict[str, Any] | None = None
    try:
        for i in range(8):
            adapter = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 is vtable slot 12; DXGI_ERROR_NOT_FOUND ends the list.
            if _method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(i, ctypes.byref(adapter)) != 0:
                break
            try:
                desc = _AdapterDesc1()
                if _method(adapter, 10, ctypes.POINTER(_AdapterDesc1))(ctypes.byref(desc)) != 0:  # GetDesc1
                    continue
                if desc.Flags & 2 or (best and desc.DedicatedVideoMemory <= best["dedicated"]):  # software adapter
                    continue
                budget = 0
                a3 = ctypes.c_void_p()
                iid = _GUID.parse("645967a4-1392-4310-a798-8053ce3e93fd")  # IDXGIAdapter3
                if _method(adapter, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.byref(iid), ctypes.byref(a3)) == 0:
                    try:
                        info = _VideoMemoryInfo()
                        # QueryVideoMemoryInfo(node 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &info): slot 14.
                        if _method(a3, 14, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(_VideoMemoryInfo))(
                                0, 0, ctypes.byref(info)) == 0:
                            budget = int(info.Budget)
                    finally:
                        _release(a3)
                best = {"name": desc.Description, "dedicated": int(desc.DedicatedVideoMemory), "budget": budget,
                        "luid": f"luid_0x{desc.LuidHigh & 0xFFFFFFFF:08x}_0x{desc.LuidLow:08x}"}
            finally:
                _release(adapter)
    finally:
        _release(factory)
    return best


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("largeValue", ctypes.c_longlong)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


def _counter_values(path: str) -> dict[str, int]:
    """Instance name -> value of one wildcard PDH counter (bytes)."""
    pdh = ctypes.WinDLL("pdh")
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(counter)) != 0:
            return {}
        if pdh.PdhCollectQueryData(query) != 0:
            return {}
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        pdh_fmt_large = 0x400
        pdh.PdhGetFormattedCounterArrayW(counter, pdh_fmt_large, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return {}
        buf = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(counter, pdh_fmt_large, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        return {items[i].szName: int(items[i].FmtValue.largeValue) for i in range(count.value)
                if items[i].szName and items[i].FmtValue.CStatus in (0, 1)}
    finally:
        pdh.PdhCloseQuery(query)


def _counters(luid: str) -> tuple[int | None, dict[int, int]]:
    """(the adapter's dedicated usage, {pid: dedicated bytes}) on the adapter with this LUID."""
    if sys.platform != "win32":
        return None, {}
    adapter = sum(v for k, v in _counter_values(r"\GPU Adapter Memory(*)\Dedicated Usage").items()
                  if k.lower().startswith(luid))
    per_pid: dict[int, int] = {}
    for name, value in _counter_values(r"\GPU Process Memory(*)\Dedicated Usage").items():
        hit = re.match(r"pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)", name.lower())
        if hit and hit.group(2) == luid and value > 0:
            per_pid[int(hit.group(1))] = per_pid.get(int(hit.group(1)), 0) + value
    return (adapter or None), per_pid


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvmlProcess(ctypes.Structure):  # nvmlProcessInfo_v1_t: the shape every driver still answers
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong)]


def _nvml(lib: Any = None) -> dict[str, Any] | None:
    """The NVIDIA card with the most memory: ``{name, total, used, per_pid}``; None without NVML."""
    try:
        lib = lib or ctypes.CDLL("libnvidia-ml.so.1")
    except OSError:
        return None
    if lib.nvmlInit_v2() != 0:
        return None
    best: dict[str, Any] | None = None
    try:
        count = ctypes.c_uint(0)
        if lib.nvmlDeviceGetCount_v2(ctypes.pointer(count)) != 0:
            return None
        for i in range(count.value):
            handle = ctypes.c_void_p()
            mem = _NvmlMemory()
            if lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.pointer(handle)) != 0:
                continue
            if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.pointer(mem)) != 0 or not mem.total:
                continue
            if best and mem.total <= best["total"]:
                continue
            name = ctypes.create_string_buffer(96)
            lib.nvmlDeviceGetName(handle, name, 96)
            per_pid: dict[int, int] = {}
            for query in ("nvmlDeviceGetGraphicsRunningProcesses", "nvmlDeviceGetComputeRunningProcesses"):
                n = ctypes.c_uint(64)
                rows = (_NvmlProcess * 64)()
                if getattr(lib, query)(handle, ctypes.pointer(n), rows) == 0:
                    for row in rows[:min(n.value, 64)]:
                        # NVML_VALUE_NOT_AVAILABLE (all ones) when the driver can't say per program.
                        if row.usedGpuMemory and row.usedGpuMemory < 1 << 63:
                            per_pid[int(row.pid)] = max(per_pid.get(int(row.pid), 0), int(row.usedGpuMemory))
            best = {"name": name.value.decode("utf-8", "replace"), "total": int(mem.total), "used": int(mem.used),
                    "per_pid": per_pid}
    except (OSError, AttributeError, ValueError):
        return best
    finally:
        lib.nvmlShutdown()
    return best


def _amd(drm: str = "/sys/class/drm") -> dict[str, Any] | None:
    """The amdgpu card with the most VRAM: ``{name, total, used, per_pid: {}}``; None without one."""
    import glob

    best: dict[str, Any] | None = None
    for device in sorted(glob.glob(os.path.join(drm, "card*", "device"))):
        try:
            with open(os.path.join(device, "mem_info_vram_total"), encoding="ascii") as fh:
                total = int(fh.read().strip())
            with open(os.path.join(device, "mem_info_vram_used"), encoding="ascii") as fh:
                used = int(fh.read().strip())
        except (OSError, ValueError):
            continue
        if total > 0 and (best is None or total > best["total"]):
            best = {"name": f"AMD {os.path.basename(os.path.dirname(device))}", "total": total, "used": used,
                    "per_pid": {}}
    return best


def _linux_card() -> dict[str, Any] | None:
    return _nvml() or _amd()


def _table() -> dict[int, Any]:
    from backend.util import proc_table

    return proc_table.table()


def _names(procs: dict[int, Any]) -> dict[int, str]:
    return {pid: re.sub(r"(?i)[.]exe$", "", p.name) for pid, p in procs.items()}


def _mine(procs: dict[int, Any]) -> set[int]:
    """This process and everything it started (its own WebView2 too, by parent pid; another
    app's WebView2 is not in its tree)."""
    try:
        from backend.util import proc_table

        return set(proc_table.tree(procs, os.getpid()))
    except Exception:
        return set()


def read(top: int = 3, procs: dict[int, Any] | None = None) -> dict[str, Any] | None:
    """``{adapter, total_gb, budget_gb, used_gb, share, ducky_gb, top: [{name, gb}]}`` for the
    main GPU, or None when Windows can't tell (no DXGI, no GPU counters, not Windows). Never
    raises. ``procs``: a process table already read (names the programs without reading another)."""
    try:
        if sys.platform.startswith("linux"):
            card = _linux_card()
            if not card:
                return None
            # The card's own total stands in for Windows' OS budget (Linux keeps none).
            adapter = {"name": card["name"], "dedicated": card["total"], "budget": card["total"]}
            used, per_pid = card["used"], dict(card["per_pid"])
        else:
            adapter = _adapter()
            if not adapter or not adapter.get("dedicated"):
                return None
            used, per_pid = _counters(str(adapter["luid"]))
        if used is None:
            used = sum(per_pid.values()) or None
        if used is None:
            return None
        if per_pid and procs is None:
            procs = _table()
        names = _names(procs) if per_pid else {}
        mine = _mine(procs) if per_pid else set()
        ducky = sum(size for pid, size in per_pid.items() if pid in mine)
        by_name: dict[str, int] = {}
        for pid, size in per_pid.items():
            name = names.get(pid) or f"pid {pid}"
            by_name[name] = by_name.get(name, 0) + size
        rows = sorted(by_name.items(), key=lambda kv: -kv[1])[:top]
        total = int(adapter["dedicated"])
        return {"adapter": str(adapter.get("name") or ""), "total_gb": round(total / _GB, 1),
                "budget_gb": round(int(adapter.get("budget") or 0) / _GB, 1), "used_gb": round(used / _GB, 1),
                "share": round(used / total, 3), "ducky_gb": round(ducky / _GB, 2), "top": [{"name": n, "gb": round(s / _GB, 1)} for n, s in rows]}
    except Exception:
        return None


def full(info: dict[str, Any] | None) -> bool:
    return bool(info) and float(info.get("share") or 0) >= FULL_SHARE


TITLE = "UEFN is nearly out of graphics memory"


def describe(info: dict[str, Any]) -> tuple[str, str]:
    """(title, body), the body under 160 characters: 'UEFN 7.1 of 8 GB dedicated (Ducky 0.1 GB).
    Lower Engine Scalability or unload regions.' (the top user first, UEFN by that name)."""
    rows = info.get("top") or []
    lead = rows[0] if rows else {}
    name = str(lead.get("name") or "")
    uefn = name.lower().startswith(_UEFN)
    who, gb = ("UEFN" if uefn else name[:30] or "Graphics"), float(lead.get("gb") or info.get("used_gb") or 0)
    fix = "Lower Engine Scalability or unload regions." if uefn else "Close games, video or 3D browser tabs."
    body = (f"{who} {gb:g} of {float(info['total_gb']):g} GB dedicated "
            f"(Ducky {float(info.get('ducky_gb') or 0):.1f} GB). {fix}")
    return TITLE, body
