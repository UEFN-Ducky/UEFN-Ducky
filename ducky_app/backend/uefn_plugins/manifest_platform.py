"""Which OS and CPU a compiled plugin build is for, read from its plugin.json.

A compiled build names its target in ``python_abi``: ``cp313-win_amd64`` (Windows, the only
target before Linux builds), ``cpython-313-x86_64-linux-gnu`` or ``cpython-313-aarch64-linux-gnu``.
A compiled build with no ``python_abi`` is an old Windows build. Source plugins run anywhere.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

WINDOWS = "windows"
LINUX = "linux"
X86_64 = "x86_64"
AARCH64 = "aarch64"

_ARCH_ALIASES = {"amd64": X86_64, "x86_64": X86_64, "x64": X86_64, "arm64": AARCH64, "aarch64": AARCH64}
_OS_LABELS = {WINDOWS: "Windows", LINUX: "Linux"}


def norm_arch(raw: str) -> str:
    return _ARCH_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())


def host_os() -> str:
    if sys.platform == "win32":
        return WINDOWS
    if sys.platform.startswith("linux"):
        return LINUX
    return sys.platform


def host_arch() -> str:
    return norm_arch(platform.machine())


def python_abi(os_name: str, arch: str, mm: tuple[int, int] | None = None) -> str:
    """The ABI tag a compiled module for this target carries in its file name."""
    major, minor = mm or sys.version_info[:2]
    arch = norm_arch(arch)
    if os_name == WINDOWS:
        return f"cp{major}{minor}-win_amd64"
    if os_name == LINUX and arch in (X86_64, AARCH64):
        return f"cpython-{major}{minor}-{arch}-linux-gnu"
    raise ValueError(f"no compiled plugin builds for {os_name} {arch}")


def module_suffix(os_name: str) -> str:
    return ".pyd" if os_name == WINDOWS else ".so"


def abi_target(abi: str) -> tuple[str, str] | None:
    """(os, arch) of an ABI tag, None when it names no target we know."""
    abi = str(abi or "").strip().lower()
    if abi.endswith("-win_amd64"):
        return WINDOWS, X86_64
    for arch in (X86_64, AARCH64):
        if abi.endswith(f"-{arch}-linux-gnu"):
            return LINUX, arch
    return None


def _is_compiled(manifest: dict[str, Any]) -> bool:
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    return bool(manifest.get("compiled")) or bool(str(manifest.get("python_abi") or "").strip()) or bool(
        str(backend.get("compiled") or "").strip()
    )


def manifest_target(manifest: dict[str, Any]) -> tuple[str, str] | None:
    """(os, arch) a plugin build is for; None for a source plugin (runs anywhere)."""
    if not _is_compiled(manifest):
        return None
    abi = str(manifest.get("python_abi") or "").strip()
    if not abi:
        return WINDOWS, X86_64  # compiled builds before python_abi were all Windows
    return abi_target(abi) or ("", "")


def platform_label(manifest: dict[str, Any]) -> str:
    """'Windows only' / 'Linux x86_64 only' for a compiled build, '' for source."""
    target = manifest_target(manifest)
    if target is None:
        return ""
    os_name, arch = target
    if os_name == WINDOWS:
        return "Windows only"
    return f"{_OS_LABELS.get(os_name, os_name or 'Unknown')} {arch} only".strip()


def platform_problem(manifest: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> str:
    """Why this build can't load here, '' when it can."""
    target = manifest_target(manifest)
    if target is None:
        return ""
    want = (os_name or host_os(), norm_arch(arch or host_arch()))
    if target == want:
        return ""
    label = _OS_LABELS.get(want[0], want[0])
    if target[0] == want[0]:
        return f"This plugin has no {label} {want[1]} build yet"
    return f"This plugin has no {label} build yet"
