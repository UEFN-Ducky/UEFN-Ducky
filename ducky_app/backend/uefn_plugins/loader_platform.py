"""Can this machine load a plugin build, and where is its compiled module?

Windows loads ``<module>*.pyd``, Linux ``<module>*.so``; source plugins load everywhere.
A build for another OS or CPU is refused with a plain reason (shown on the Store card)
instead of crashing in the import: 'This plugin has no Linux build yet'.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.uefn_plugins import manifest_platform as mp


class PlatformError(RuntimeError):
    """The plugin has no build for this OS/CPU."""


def load_problem(manifest: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> str:
    return mp.platform_problem(manifest, os_name, arch)


def check_loadable(manifest: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> None:
    problem = load_problem(manifest, os_name, arch)
    if problem:
        raise PlatformError(problem)


def card_badge(manifest: dict[str, Any], os_name: str | None = None, arch: str | None = None) -> str:
    """What the Store card shows for a build this machine can't load ('Windows only'), else ''."""
    return mp.platform_label(manifest) if load_problem(manifest, os_name, arch) else ""


def store_badge(plugin_id: str) -> str:
    """The Store row's badge for an installed plugin whose build can't load here, else ''.

    ponytail: installed plugins only; catalog rows get one when the Store sends each
    record's python_abi (the install already picks the right artifact)."""
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        return card_badge(load_plugin_manifest(plugin_id) or {})
    except Exception:  # noqa: BLE001 - an unreadable manifest gets no badge, the row still shows
        return ""


def find_compiled(root: Path, manifest: dict[str, Any], mod_name: str, os_name: str | None = None) -> Path | None:
    """The compiled module at the plugin root: ``backend.compiled`` when present, else the
    newest ``<mod_name>*<suffix>`` for this OS."""
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    suffix = mp.module_suffix(os_name or mp.host_os())
    rel = str(backend.get("compiled") or "").strip()
    if rel.endswith(suffix) and (root / rel).is_file():
        return root / rel
    found = sorted((p for p in root.glob(f"{mod_name}*{suffix}") if p.is_file()), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None
