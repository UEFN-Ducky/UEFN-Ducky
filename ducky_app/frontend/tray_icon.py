"""System tray + shared app icon (Windows when pystray + Pillow are available)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

if TYPE_CHECKING:
    import tkinter as tk

ConnectionIcon = Literal["online", "wedged", "offline"]

_STATUS_PNG: dict[ConnectionIcon, str] = {
    "online": "OnlineMCPIcon.png",
    "wedged": "WedgedMCPIcon.png",
    "offline": "OfflineMCPIcon.png",
}

_STATUS_ALIASES: dict[str, ConnectionIcon] = {
    "ok": "online",
    "partial": "wedged",
    "offline": "offline",
    "idle": "offline",
}

_ICO_SIZES = (16, 32, 48, 64, 128, 256)
_TRAY_SIZE = 64


def _imports_ok() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def tray_supported() -> bool:
    """System tray on Windows when pystray + Pillow are installed."""
    return sys.platform == "win32" and _imports_ok()


def _normalize_mode(mode: str | ConnectionIcon) -> ConnectionIcon:
    if mode in _STATUS_PNG:
        return mode  # type: ignore[return-value]
    return _STATUS_ALIASES.get(mode, "offline")


def _ui_web_root() -> Path:
    return Path(__file__).resolve().parent / "ui_web" / "web"


def _png_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    try:
        from frontend.bundle_root import is_packaged_runtime, packaged_data_root

        if is_packaged_runtime():
            root = packaged_data_root()
            if root:
                dirs.append(root / "frontend" / "ui_web" / "web" / "dist")
    except Exception:
        pass

    web = _ui_web_root()
    dirs.extend((web / "dist", web / "public"))
    return dirs


def resolve_status_png_path(mode: ConnectionIcon) -> Path | None:
    """Resolve bundled or dev PNG for a connection state."""
    mode = _normalize_mode(mode)
    name = _STATUS_PNG[mode]
    for directory in _png_search_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def write_ico_from_png(dest: Path, png: Path) -> None:
    """Write multi-size ``.ico`` from a PNG source, preserving alpha transparency."""
    from PIL import Image

    src = Image.open(png).convert("RGBA")
    images: list[Image.Image] = []
    for sz in _ICO_SIZES:
        images.append(src.resize((sz, sz), Image.Resampling.LANCZOS))
    images[-1].save(
        dest,
        format="ICO",
        sizes=[(sz, sz) for sz in _ICO_SIZES],
        append_images=images[:-1],
    )


def write_ico(path: Path, mode: ConnectionIcon = "online") -> None:
    """Write multi-size ``.ico`` for PyInstaller + Windows shell."""
    mode = _normalize_mode(mode)
    png = resolve_status_png_path(mode)
    if not png:
        raise FileNotFoundError(f"Missing PNG for connection mode: {mode}")
    write_ico_from_png(path, png)


def resolve_app_icon_path(mode: ConnectionIcon = "online") -> Path | None:
    """``.ico`` for Windows taskbar / pywebview / Explorer (cached per mode)."""
    import tempfile

    mode = _normalize_mode(mode)
    png = resolve_status_png_path(mode)
    if not png:
        return None

    # Packaged build ships a single online-branded app_icon.ico for the EXE file icon.
    if mode == "online":
        try:
            from frontend.bundle_root import is_packaged_runtime, packaged_data_root

            if is_packaged_runtime():
                root = packaged_data_root()
                if root:
                    bundled = root / "frontend" / "app_icon.ico"
                    if bundled.is_file():
                        return bundled
        except Exception:
            pass

        here = Path(__file__).resolve()
        for candidate in (
            here.parents[1] / "build" / "app_icon.ico",
            here.parents[2] / "build" / "app_icon.ico",
        ):
            if candidate.is_file():
                return candidate

    try:
        out = Path(tempfile.gettempdir()) / f"uefn_ducky_{mode}.ico"
        if not out.is_file() or png.stat().st_mtime > out.stat().st_mtime:
            write_ico_from_png(out, png)
        return out if out.is_file() else None
    except Exception:
        return None


def load_status_image(mode: ConnectionIcon):
    """PIL image for pystray (64×64)."""
    from PIL import Image

    mode = _normalize_mode(mode)
    png = resolve_status_png_path(mode)
    if not png:
        raise FileNotFoundError(f"Missing PNG for connection mode: {mode}")
    img = Image.open(png).convert("RGBA")
    if img.size != (_TRAY_SIZE, _TRAY_SIZE):
        img = img.resize((_TRAY_SIZE, _TRAY_SIZE), Image.Resampling.LANCZOS)
    return img


class TrayIconController:
    """Runs pystray in a background thread; menu callbacks run directly (no Tk marshalling)."""

    def __init__(
        self,
        root: "tk.Tk",
        *,
        on_show: Callable[[], None],
        on_exit: Callable[[], None],
        tooltip: str,
        status: str = "idle",
    ) -> None:
        import pystray

        self._root = root
        self._pystray = pystray
        self._on_show = on_show
        self._on_exit = on_exit
        self._mode = _normalize_mode(status)

        menu = pystray.Menu(
            pystray.MenuItem("Open UEFN Ducky", self._cb_show, default=True),
            pystray.MenuItem("Exit all", self._cb_exit),
        )
        self._icon = pystray.Icon(
            "uefn_ducky",
            load_status_image(self._mode),
            tooltip,
            menu,
        )

    def set_connection_mode(self, mode: str | ConnectionIcon) -> None:
        """Swap tray icon to match connection state (safe from any thread)."""
        mode = _normalize_mode(mode)
        if mode == self._mode:
            return
        self._mode = mode
        try:
            self._icon.icon = load_status_image(mode)
        except Exception:
            pass

    def set_status(self, status: str) -> None:
        """Maps ok/partial/offline saved status strings to connection modes."""
        self.set_connection_mode(status)

    def _dispatch(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, name="tray-cb", daemon=True).start()

    def _cb_show(self, _icon: object, _item: object | None = None) -> None:
        self._dispatch(self._on_show)

    def _cb_exit(self, _icon: object, _item: object | None = None) -> None:
        self._dispatch(self._on_exit)

    def run_daemon(self) -> None:
        t = threading.Thread(target=self._icon.run, name="uefn-ducky-tray", daemon=True)
        t.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass
