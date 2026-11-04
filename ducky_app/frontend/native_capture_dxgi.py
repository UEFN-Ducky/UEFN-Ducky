"""DXGI Desktop Duplication grab of a desktop rect: the GPU path to 60 fps (ctypes only).

GDI BitBlt waits on the compositor in 10 ms steps (Nov 1 2026: 822x1032 10 ms, 1920x1080
20 ms, 3840x1080 30 ms), so a 1080p UEFN window topped out near 48 fps. Duplication hands
over each composed desktop frame as a GPU texture; only the watched rect is copied to a
staging texture and read back. Same interface as native_capture._Grabber: grab_into() writes
the rect straight into the caller's buffer (no bytes copy, GIL released) and returns False when
nothing changed on screen since the last frame (nothing to send); grab() returns None then.

ponytail: outputs of the first adapter only and no rotation; anything else raises and the
caller falls back to BitBlt.
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

_HR = ctypes.c_long
WAIT_TIMEOUT = ctypes.c_long(0x887A0027).value
ACCESS_LOST = ctypes.c_long(0x887A0026).value

IID_IDXGIDevice = "54ec77fa-1377-44e6-8c32-88fd5f44c84c"
IID_IDXGIOutput1 = "00cddea8-939b-4b83-a340-a685226666cc"
IID_ID3D11Texture2D = "6f15aaf2-d208-4e89-9ab4-489535d34f9c"


class _GUID(ctypes.Structure):
    _fields_ = [("b", ctypes.c_ubyte * 16)]


def _guid(s: str) -> _GUID:
    g = _GUID()
    ctypes.memmove(g.b, uuid.UUID(s).bytes_le, 16)
    return g


class _OutputDesc(ctypes.Structure):
    _fields_ = [
        ("DeviceName", wintypes.WCHAR * 32),
        ("DesktopCoordinates", wintypes.RECT),
        ("AttachedToDesktop", wintypes.BOOL),
        ("Rotation", wintypes.UINT),
        ("Monitor", wintypes.HMONITOR),
    ]


class _FrameInfo(ctypes.Structure):
    _fields_ = [
        ("LastPresentTime", ctypes.c_int64),
        ("LastMouseUpdateTime", ctypes.c_int64),
        ("AccumulatedFrames", wintypes.UINT),
        ("RectsCoalesced", wintypes.BOOL),
        ("ProtectedContentMaskedOut", wintypes.BOOL),
        ("PointerX", wintypes.LONG),
        ("PointerY", wintypes.LONG),
        ("PointerVisible", wintypes.BOOL),
        ("TotalMetadataBufferSize", wintypes.UINT),
        ("PointerShapeBufferSize", wintypes.UINT),
    ]


class _TexDesc(ctypes.Structure):
    _fields_ = [
        ("Width", wintypes.UINT),
        ("Height", wintypes.UINT),
        ("MipLevels", wintypes.UINT),
        ("ArraySize", wintypes.UINT),
        ("Format", wintypes.UINT),
        ("SampleCount", wintypes.UINT),
        ("SampleQuality", wintypes.UINT),
        ("Usage", wintypes.UINT),
        ("BindFlags", wintypes.UINT),
        ("CPUAccessFlags", wintypes.UINT),
        ("MiscFlags", wintypes.UINT),
    ]


class _Box(ctypes.Structure):
    _fields_ = [(n, wintypes.UINT) for n in ("left", "top", "front", "right", "bottom", "back")]


class _Mapped(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", wintypes.UINT), ("DepthPitch", wintypes.UINT)]


def _vcall(obj: ctypes.c_void_p, idx: int, argtypes: list, *args, restype=_HR):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[idx])(obj, *args)


def _ok(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed: 0x{hr & 0xFFFFFFFF:08X}")


def _release(obj: ctypes.c_void_p | None) -> None:
    if obj and obj.value:
        _vcall(obj, 2, [], restype=wintypes.ULONG)


def _qi(obj: ctypes.c_void_p, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    g = _guid(iid)
    _ok(_vcall(obj, 0, [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(g), ctypes.byref(out)), "QueryInterface")
    return out


class _Output:
    def __init__(self, dev: ctypes.c_void_p, output: ctypes.c_void_p, rect: tuple[int, int, int, int]) -> None:
        self.rect = rect  # left, top, right, bottom on the virtual desktop
        self._dev = dev
        self._out1 = _qi(output, IID_IDXGIOutput1)
        self.dup = ctypes.c_void_p()
        self.open()

    def open(self) -> None:
        _release(self.dup)
        self.dup = ctypes.c_void_p()
        _ok(
            _vcall(self._out1, 22, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], self._dev, ctypes.byref(self.dup)),
            "DuplicateOutput",
        )

    def close(self) -> None:
        _release(self.dup)
        _release(self._out1)


class DxgiGrabber:
    def __init__(self) -> None:
        d3d11 = ctypes.windll.d3d11
        self._dev = ctypes.c_void_p()
        self._ctx = ctypes.c_void_p()
        level = wintypes.UINT()
        # Hardware driver, BGRA support, SDK version 7.
        _ok(
            d3d11.D3D11CreateDevice(
                None, 1, None, 0x20, None, 0, 7, ctypes.byref(self._dev), ctypes.byref(level), ctypes.byref(self._ctx)
            ),
            "D3D11CreateDevice",
        )
        self._outputs: list[_Output] = []
        dxgi_dev = _qi(self._dev, IID_IDXGIDevice)
        adapter = ctypes.c_void_p()
        try:
            _ok(_vcall(dxgi_dev, 7, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(adapter)), "GetAdapter")
            i = 0
            while True:
                out = ctypes.c_void_p()
                if _vcall(adapter, 7, [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)], i, ctypes.byref(out)) < 0:
                    break  # DXGI_ERROR_NOT_FOUND: no more outputs
                try:
                    desc = _OutputDesc()
                    _ok(_vcall(out, 7, [ctypes.POINTER(_OutputDesc)], ctypes.byref(desc)), "GetDesc")
                    r = desc.DesktopCoordinates
                    if desc.AttachedToDesktop and desc.Rotation in (0, 1):  # unspecified / identity
                        self._outputs.append(_Output(self._dev, out, (r.left, r.top, r.right, r.bottom)))
                finally:
                    _release(out)
                i += 1
        finally:
            _release(adapter)
            _release(dxgi_dev)
        if not self._outputs:
            self.close()
            raise OSError("no duplicable output")
        self._staging = ctypes.c_void_p()
        self._staging_size = (0, 0)
        self._out_buf = ctypes.create_string_buffer(0)
        self._rect: tuple[int, int, int, int] | None = None  # rect the last frame was read for

    def _output_for(self, left: int, top: int, w: int, h: int) -> _Output | None:
        for o in self._outputs:
            ol, ot, orr, ob = o.rect
            if ol <= left and ot <= top and left + w <= orr and top + h <= ob:
                return o
        return None

    def _ensure_staging(self, w: int, h: int) -> None:
        if self._staging_size == (w, h):
            return
        _release(self._staging)
        self._staging = ctypes.c_void_p()
        # B8G8R8A8_UNORM, staging usage, CPU read.
        desc = _TexDesc(w, h, 1, 1, 87, 1, 0, 3, 0, 0x20000, 0)
        _ok(
            _vcall(self._dev, 5, [ctypes.POINTER(_TexDesc), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(desc), None, ctypes.byref(self._staging)),
            "CreateTexture2D",
        )
        self._staging_size = (w, h)

    def grab(self, left: int, top: int, w: int, h: int) -> bytes | None:
        if len(self._out_buf) != w * h * 4:
            self._out_buf = ctypes.create_string_buffer(w * h * 4)
        if not self.grab_into(left, top, w, h, ctypes.addressof(self._out_buf)):
            return None
        return self._out_buf.raw

    def grab_into(self, left: int, top: int, w: int, h: int, dst: int) -> bool:
        """w*h*4 BGRA bytes of the rect at address dst; False: nothing new on screen."""
        out = self._output_for(left, top, w, h)
        if out is None:
            raise OSError("rect spans outputs")
        self._ensure_staging(w, h)
        info = _FrameInfo()
        res = ctypes.c_void_p()
        acquire = [wintypes.UINT, ctypes.POINTER(_FrameInfo), ctypes.POINTER(ctypes.c_void_p)]
        hr = _vcall(out.dup, 8, acquire, 0, ctypes.byref(info), ctypes.byref(res))
        if hr == ACCESS_LOST:
            out.open()  # mode change, secure desktop, full-screen switch
            raise OSError("access lost")
        if hr == WAIT_TIMEOUT:
            if self._rect == (left, top, w, h):
                return False  # nothing new on screen: nothing to send
            # A new viewer or a new rect on a still screen still needs its first picture:
            # a fresh duplication always starts with the whole current desktop.
            out.open()
            hr = _vcall(out.dup, 8, acquire, 100, ctypes.byref(info), ctypes.byref(res))
            if hr == WAIT_TIMEOUT:
                return False
        _ok(hr, "AcquireNextFrame")
        try:
            tex = _qi(res, IID_ID3D11Texture2D)
            try:
                ol, ot = out.rect[0], out.rect[1]
                box = _Box(left - ol, top - ot, 0, left - ol + w, top - ot + h, 1)
                # CopySubresourceRegion(dst, dstSub, x, y, z, src, srcSub, box)
                _vcall(
                    self._ctx, 46,
                    [ctypes.c_void_p, wintypes.UINT, wintypes.UINT, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(_Box)],
                    self._staging, 0, 0, 0, 0, tex, 0, ctypes.byref(box), restype=None,
                )
            finally:
                _release(tex)
        finally:
            _release(res)
            _vcall(out.dup, 14, [])  # ReleaseFrame
        self._rect = (left, top, w, h)
        self._read_into(w, h, dst)
        return True

    def _read_into(self, w: int, h: int, dst: int) -> None:
        m = _Mapped()
        # Map(resource, sub, D3D11_MAP_READ, flags, out)
        _ok(_vcall(self._ctx, 14, [ctypes.c_void_p, wintypes.UINT, wintypes.UINT, wintypes.UINT, ctypes.POINTER(_Mapped)], self._staging, 0, 1, 0, ctypes.byref(m)), "Map")
        try:
            row = w * 4
            if m.RowPitch == row:
                ctypes.memmove(dst, m.pData, row * h)
            else:
                for y in range(h):
                    ctypes.memmove(dst + y * row, m.pData + y * m.RowPitch, row)
        finally:
            _vcall(self._ctx, 15, [ctypes.c_void_p, wintypes.UINT], self._staging, 0, restype=None)

    def close(self) -> None:
        for o in getattr(self, "_outputs", []):
            o.close()
        self._outputs = []
        _release(getattr(self, "_staging", None))
        _release(self._ctx)
        _release(self._dev)
