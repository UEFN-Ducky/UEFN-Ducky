"""Is Microsoft Edge WebView2 Runtime on this PC, and new enough to show Ducky's window?

Without it pywebview falls back to the old Internet Explorer engine: the window opens, asks for
the app's page files and never runs them, so Ducky sat on its loading screen for 60 s and then
filed "stuck on its loading screen" (Nov 11 2026, 1.2.485: the fast launcher had already said
"exit 20 webview2_missing"). Tools that remove Microsoft Edge take the runtime with it. Checked
before the window starts, the person gets one message that says what to install.

The check is the one pywebview makes to pick its engine (webview/platforms/winforms.py
``_is_chromium``): the runtime or a Beta / Dev / Canary channel at ``MIN_VERSION`` or newer,
registered for the PC or for this user.
"""

from __future__ import annotations

import sys

# The floor pywebview and the fast launcher (native_launcher window/src/version.rs) both use.
MIN_VERSION = "86.0.622.0"
DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"
ERROR_CODE = "webview2_missing"  # its own error code, apart from other startup errors
_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # WebView2 Runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
)
_ROOTS = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients", r"SOFTWARE\Microsoft\EdgeUpdate\Clients")


class WebView2Missing(RuntimeError):
    """The runtime is missing or too old; ``str()`` is the whole message for the person."""


def _numbers(version: str) -> tuple[int, ...] | None:
    first = (version or "").split()[0] if (version or "").split() else ""
    try:
        return tuple(int(part) for part in first.split(".")) if first else None
    except ValueError:
        return None


def version_at_least(found: str, minimum: str = MIN_VERSION) -> bool:
    """True when ``found`` is a well-formed version at or above ``minimum`` (missing parts are 0)."""
    a, b = _numbers(found), _numbers(minimum)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) >= b + (0,) * (width - len(b))


def registered_versions() -> list[str]:
    """Every WebView2 version this PC has registered (``pv``), in no order. [] when none."""
    try:
        import winreg
    except ImportError:
        return []
    found: list[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for root in _ROOTS:
            for client in _CLIENTS:
                try:
                    with winreg.OpenKey(hive, rf"{root}\{client}") as key:
                        value, _kind = winreg.QueryValueEx(key, "pv")
                except OSError:
                    continue
                text = str(value or "").strip()
                if text and text != "0.0.0.0":
                    found.append(text)
    return found


def problem(versions: list[str] | None = None) -> str:
    """"" when the window can start; else the message for the person."""
    if sys.platform != "win32" and versions is None:
        return ""
    found = registered_versions() if versions is None else versions
    if any(version_at_least(v) for v in found):
        return ""
    newest = max((v for v in found if _numbers(v)), key=lambda v: _numbers(v) or (), default="")
    what = (f"this PC has an old one (version {newest})" if newest
            else "this PC doesn't have it. Tools that remove Microsoft Edge take it out too")
    return (
        f"UEFN Ducky needs Microsoft Edge WebView2 Runtime to show its window, and {what}.\n\n"
        f"Get the Evergreen installer from Microsoft: {DOWNLOAD_PAGE}\n"
        "Install it, then start UEFN Ducky again. Your chats and settings are kept."
    )


def require() -> None:
    """Raise :class:`WebView2Missing` when the window could only start on the old IE engine."""
    message = problem()
    if message:
        raise WebView2Missing(message)
