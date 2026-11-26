"""Windows Controlled folder access (Defender's ransomware protection) lets only trusted apps
write into protected folders such as Documents, where Fortnite Projects live. Turned on, it
refuses UEFN Ducky's and its agents' saves with a bare "Access is denied".

Ducky never asks about it on its own (no box, no admin prompt, no restart): Windows already shows
its own "Unauthorized changes blocked" notice, as it does for every other app. A refused write
just says what it was and how to allow Ducky (refusal_hint), only when Defender's log
(event 1123, readable without admin rights) shows it refused that folder.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

LOG = "Microsoft-Windows-Windows Defender/Operational"
BLOCKED_EVENT = 1123
BY_HAND = ("Open Windows Security → Virus & threat protection → Ransomware protection → Allow an "
           "app through Controlled folder access, and add UEFN-Ducky.exe (and the agent's program).")
REFUSED_HINT = ("Windows Controlled folder access (ransomware protection) refused this write. To let "
                "UEFN Ducky save there, " + BY_HAND[0].lower() + BY_HAND[1:]
                + " Add UEFN-Ducky-Bridge.exe too, then restart UEFN Ducky.")
REFUSAL_WINDOW_S = 120


def _dos_path(path: str) -> str:
    r"""``\Device\HarddiskVolume3\x`` as ``C:\x`` (Defender writes either form)."""
    if not path.lower().startswith("\\device\\") or os.name != "nt":
        return path
    import ctypes
    import string

    buf = ctypes.create_unicode_buffer(1024)
    for letter in string.ascii_uppercase:
        if ctypes.windll.kernel32.QueryDosDeviceW(f"{letter}:", buf, len(buf)):
            device = buf.value
            if path.lower().startswith(device.lower() + "\\"):
                return f"{letter}:" + path[len(device):]
    return path


def parse_blocked(xml: str) -> list[tuple[str, str, str]]:
    """``(time, program, refused file)`` per Controlled folder access block in ``wevtutil /f:xml`` text."""
    found = []
    for event in xml.split("</Event>"):
        when = re.search(r"<TimeCreated SystemTime='([^']+)'", event)
        name = re.search(r"<Data Name='Process Name'>([^<]+)</Data>", event)
        target = re.search(r"<Data Name='Path'>([^<]*)</Data>", event)
        if when and name:
            found.append((when.group(1), _dos_path(html.unescape(name.group(1)).strip()),
                          _dos_path(html.unescape(target.group(1)).strip()) if target else ""))
    return found


def _blocks(window_ms: int) -> list[tuple[str, str, str]]:
    if os.name != "nt":
        return []
    query = f"*[System[(EventID={BLOCKED_EVENT}) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    try:
        out = subprocess.run(["wevtutil", "qe", LOG, f"/q:{query}", "/c:200", "/rd:true", "/f:xml"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_blocked(out.stdout or "")


def refusal_hint(path: str | Path, *, blocks: Callable[[int], list[tuple[str, str, str]]] = _blocks) -> str:
    """What to say about a refused write into ``path`` (a file or its folder): REFUSED_HINT when
    Windows' Controlled folder access refused a write in that folder within the last two minutes,
    else "" (folder permissions: not ours to explain)."""
    if os.name != "nt":
        return ""
    target = Path(path)
    folders = {os.path.normcase(str(target)), os.path.normcase(str(target.parent))}
    if not any(os.path.normcase(str(Path(refused).parent)) in folders
               for _, _, refused in blocks(REFUSAL_WINDOW_S * 1000) if refused):
        return ""
    return REFUSED_HINT


# --- Linux: pickers through the XDG desktop portal ------------------------------

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
_RESPONSE = re.compile(r"^(\S+): org\.freedesktop\.portal\.Request\.Response \(uint32 (\d+), (.*)\)$")


def portal_pick(title: str = "Add project", *, directory: bool = True) -> str | None:
    """The desktop's own file or folder picker (GNOME, KDE, Flatpak, Wayland) through
    ``org.freedesktop.portal.FileChooser.OpenFile``. The chosen path, None when cancelled
    or there is no portal."""
    from urllib.parse import unquote, urlparse

    from frontend.open_files import gdbus_call, gdbus_monitor, gv_str

    monitor = gdbus_monitor(PORTAL)  # first: the answer is a signal on the request object
    if monitor is None:
        return None
    try:
        opts = "{'directory': <%s>, 'modal': <true>}" % ("true" if directory else "false")
        reply = gdbus_call(PORTAL, PORTAL_PATH, "org.freedesktop.portal.FileChooser.OpenFile",
                           gv_str(""), gv_str(title), opts, timeout_s=30)
        handle = re.search(r"objectpath '([^']+)'", reply or "")
        if not handle:
            return None
        # ponytail: waits as long as the picker stays open; no timeout, the person closes it.
        for line in monitor.stdout:
            m = _RESPONSE.match(line.strip())
            if not m or m.group(1) != handle.group(1):
                continue
            uri = re.search(r"'uris': <\['([^']+)'", m.group(3))
            if m.group(2) != "0" or not uri:
                return None  # 1 cancelled, 2 failed
            parsed = urlparse(uri.group(1))
            return unquote(parsed.path) if parsed.scheme == "file" else None
        return None
    finally:
        try:
            monitor.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
