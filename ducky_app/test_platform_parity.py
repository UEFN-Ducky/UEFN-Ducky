"""Linux has every Windows tool and setting, with the same argument schemas.

Each platform's catalog is built in a fresh process that is told it runs on that
platform before anything of the app is imported (``sys.platform`` and
``platform.system()``, which ``platform_info`` and every older check read), so
import-time choices (which tools register, which settings exist) follow it. On a host
of the other OS, the few OS-only stdlib modules are stubbed so imports get through;
nothing is called.

Every difference must be listed in ``ALLOWED_DIFFERENCES`` with its reason. Until the
Linux seats land this is expected to fail; it reports as xfail unless
``DUCKY_PARITY_STRICT=1`` (set in CI from the full test pass on).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent

# name -> why it exists on one platform only. Tool names and settings field names share
# this list; a name listed here must really differ, or the entry is stale.
ALLOWED_DIFFERENCES: dict[str, str] = {}

STRICT = os.environ.get("DUCKY_PARITY_STRICT") == "1"
pytestmark = pytest.mark.xfail(not STRICT, reason="Linux parity lands seat by seat; strict from the full test pass")

PLATFORMS = {"windows": ("win32", "Windows"), "linux": ("linux", "Linux")}

_CHILD = r"""
import asyncio, json, platform, sys, types

plat, system = sys.argv[1], sys.argv[2]
host_windows = sys.platform == "win32"
sys.platform = plat
platform.system = lambda: system
if plat == "win32" and not host_windows:
    for name in ("winreg", "msvcrt", "_winapi", "winsound"):
        sys.modules.setdefault(name, types.ModuleType(name))
if plat != "win32" and host_windows:
    for name in ("fcntl", "termios", "pty", "grp", "pwd", "resource"):
        sys.modules.setdefault(name, types.ModuleType(name))
sys.path.insert(0, sys.argv[3])

from backend.server import mcp
import backend.tools  # noqa: F401 - registers the core tools
from frontend.settings_schema import settings_schema

tools = {t.name: t.inputSchema for t in asyncio.run(mcp.list_tools())}
settings = {k: {"type": v["type"], "tab": v["tab"], "settable": v["settable"]} for k, v in settings_schema()["fields"].items()}
print("\n@@CATALOG@@" + json.dumps({"tools": tools, "settings": settings}, sort_keys=True, default=str))
"""


def _catalog(name: str, tmp: Path) -> dict:
    plat, system = PLATFORMS[name]
    root = tmp / name
    (root / "Roaming").mkdir(parents=True)
    env = {
        **os.environ,
        "LOCALAPPDATA": str(root), "APPDATA": str(root / "Roaming"), "HOME": str(root),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
        "UEFN_DUCKY_DOCUMENTS_DIR": str(root / "Documents"), "DUCKY_SEARCH_AUTOINDEX": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, plat, system, str(APP)],
        cwd=str(APP), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    assert done.returncode == 0 and "@@CATALOG@@" in done.stdout, f"{name} catalog failed:\n{done.stderr[-4000:]}"
    catalog = json.loads(done.stdout.rsplit("@@CATALOG@@", 1)[1])
    assert len(catalog["tools"]) > 50 and len(catalog["settings"]) > 50, f"{name}: a catalog that small proves nothing"
    return catalog


@pytest.fixture(scope="module")
def catalogs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    tmp = tmp_path_factory.mktemp("parity")
    return {name: _catalog(name, tmp) for name in PLATFORMS}


def _only_on(catalogs: dict[str, dict], kind: str) -> dict[str, set[str]]:
    win, lin = set(catalogs["windows"][kind]), set(catalogs["linux"][kind])
    return {"windows": win - lin, "linux": lin - win}


@pytest.mark.parametrize("kind", ["tools", "settings"])
def test_same_names_on_both(catalogs: dict[str, dict], kind: str) -> None:
    only = _only_on(catalogs, kind)
    unexplained = {side: sorted(n for n in names if n not in ALLOWED_DIFFERENCES) for side, names in only.items()}
    assert unexplained == {"windows": [], "linux": []}, (
        f"{kind} on one platform only; add each to ALLOWED_DIFFERENCES with a reason, or close the gap"
    )


def test_same_tool_arguments(catalogs: dict[str, dict]) -> None:
    win, lin = catalogs["windows"]["tools"], catalogs["linux"]["tools"]
    differ = sorted(n for n in set(win) & set(lin) if win[n] != lin[n] and n not in ALLOWED_DIFFERENCES)
    assert differ == [], f"tools whose argument schema differs between Windows and Linux: {differ}"


def test_same_settings_shape(catalogs: dict[str, dict]) -> None:
    win, lin = catalogs["windows"]["settings"], catalogs["linux"]["settings"]
    differ = sorted(n for n in set(win) & set(lin) if win[n] != lin[n] and n not in ALLOWED_DIFFERENCES)
    assert differ == [], f"settings whose type, tab or settable flag differs: {differ}"


def test_every_allowed_difference_has_a_reason_and_still_differs(catalogs: dict[str, dict]) -> None:
    assert all(str(why).strip() for why in ALLOWED_DIFFERENCES.values())
    differing: set[str] = set()
    for kind in ("tools", "settings"):
        for names in _only_on(catalogs, kind).values():
            differing |= names
        win, lin = catalogs["windows"][kind], catalogs["linux"][kind]
        differing |= {n for n in set(win) & set(lin) if win[n] != lin[n]}
    assert sorted(set(ALLOWED_DIFFERENCES) - differing) == [], "stale ALLOWED_DIFFERENCES entries"
