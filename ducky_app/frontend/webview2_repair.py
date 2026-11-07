"""Put back a WebView2 interop DLL an interrupted Setup left out.

Setup deletes ``_internal`` first and then copies it back (UEFN-Ducky.iss InstallDelete). A run
that stops part way leaves the files it had not copied yet missing, and ``webview/lib`` comes
late: pywebview then fails at start with "Cannot find Microsoft.Web.WebView2.Core.dll".
Another app folder on this PC (a downloaded version, the installed one) usually still has it.
When none does, the start error tells the person to run Setup again
(``shutdown._format_fatal_message``).
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

LIB = Path("_internal") / "webview" / "lib"
# What pywebview's edgechromium backend loads: both assemblies, and every runtimes/<arch>/native
# folder (it adds all three to PATH and raises when one is missing).
DLLS = (
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "runtimes/win-x64/native/WebView2Loader.dll",
    "runtimes/win-arm64/native/WebView2Loader.dll",
    "runtimes/win-x86/native/WebView2Loader.dll",
)


def missing(app_dir: Path) -> list[str]:
    """The DLLs ``app_dir`` (the folder of UEFN-Ducky.exe) lacks."""
    return [name for name in DLLS if not (app_dir / LIB / name).is_file()]


def _other_app_folders() -> list[Path]:
    try:
        from frontend.app_stage import local_sources

        return local_sources()
    except Exception:
        return []


def ensure(app_dir: Path | None = None, sources: list[Path] | None = None) -> list[str]:
    """Copy each missing DLL from another app folder; returns the ones still missing. Never raises.

    The normal start is three file checks; the folder search only runs when one is missing.
    """
    if app_dir is None:
        if not getattr(sys, "frozen", False):
            return []
        app_dir = Path(sys.executable).parent
    try:
        gone = missing(app_dir)
        if not gone:
            return []
        here = app_dir.resolve()
        for folder in _other_app_folders() if sources is None else sources:
            try:
                if folder.resolve() == here:
                    continue
            except OSError:
                continue
            for name in list(gone):
                src = folder / LIB / name
                if not src.is_file():
                    continue
                dest = app_dir / LIB / name
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    gone.remove(name)
                    logging.getLogger(__name__).warning("restored %s from %s", name, folder)
                except OSError:
                    continue
            if not gone:
                break
        return gone
    except Exception:
        logging.getLogger(__name__).warning("WebView2 DLL repair failed", exc_info=True)
        return []
