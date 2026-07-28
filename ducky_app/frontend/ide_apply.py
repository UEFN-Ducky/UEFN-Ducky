"""Apply the UEFN MCP bridge block into IDE configs (Cursor / Claude / Antigravity).

Owned by Store gateway plugins via ``api.register_ide_hookup``. Host still writes
the bridge + skills; plugins decide *which* IDEs are active. Used from Settings →
LLMs → provider detail, plugin register (auto-apply), and panel startup.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from frontend.ide_paths import IdeKind, path_for_ide
from frontend.mcp_block import build_uefn_server_block
from frontend.merge import merge_uefn_into_config
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
from frontend.skill_deploy import sync_skill_for_ide

ALL_IDES = (IdeKind.CURSOR, IdeKind.CLAUDE, IdeKind.ANTIGRAVITY)


def _active_ide_kinds() -> tuple[IdeKind, ...]:
    """Prefer Store gateway hookups; always fall back to every host IDE.

    Auto MCP/skills on startup must keep working like pre-gateway peel — an empty
    hookup list (plugins still loading) must not skip Apply.
    """
    try:
        from backend.uefn_plugins.host import get_contributions

        kinds: list[IdeKind] = []
        for row in get_contributions().get("ide_hookups") or []:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("kind") or "").strip().lower()
            try:
                kinds.append(IdeKind(raw))
            except ValueError:
                continue
        if kinds:
            return tuple(dict.fromkeys(kinds))
    except Exception:
        pass
    return ALL_IDES


def apply_ide_bridge(kind: IdeKind, settings: Optional[PanelSettings] = None) -> str:
    """Write the current bridge block into one IDE's config; returns the config path."""
    from frontend.merge import uefn_block_matches

    s = settings or replace(PanelSettings.load(), port=PANEL_LISTENER_PORT)
    block = build_uefn_server_block(s)
    path = path_for_ide(kind, s.antigravity_config_path)
    ok, _detail = uefn_block_matches(path, block)
    if ok:
        # Already pointed at this exe — do not touch mcp.json (Cursor reconnect).
        return str(path)
    merge_uefn_into_config(path, block, dry_run=False)
    for _ln in sync_skill_for_ide(path):
        pass
    return str(path)


def apply_all_ide_bridges(log: Optional[Callable[[str], None]] = None) -> list[str]:
    """Re-point every IDE at the current exe. Best-effort, never raises."""
    s = replace(PanelSettings.load(), port=PANEL_LISTENER_PORT)
    out: list[str] = []
    for kind in _active_ide_kinds():
        try:
            path = apply_ide_bridge(kind, s)
            out.append(f"Bridge → {kind.value}: {path}")
        except Exception as exc:  # noqa: BLE001 - one bad IDE must not block the others
            out.append(f"{kind.value}: {exc}")
    if log is not None:
        for line in out:
            log(line)
    return out


def verify_ide_bridge(kind: IdeKind, settings: Optional[PanelSettings] = None) -> dict[str, str | bool]:
    """Check whether an IDE config already has the current UEFN MCP bridge block."""
    from frontend.merge import uefn_block_matches

    s = settings or replace(PanelSettings.load(), port=PANEL_LISTENER_PORT)
    expected = build_uefn_server_block(s)
    path = path_for_ide(kind, s.antigravity_config_path)
    ok, detail = uefn_block_matches(path, expected)
    return {"ok": ok, "detail": detail}


def verify_all_ide_bridges(settings: Optional[PanelSettings] = None) -> dict[str, dict[str, str | bool]]:
    """Verify every IDE bridge (hookups when present, else all host IDEs)."""
    out: dict[str, dict[str, str | bool]] = {}
    for kind in _active_ide_kinds():
        out[kind.value] = verify_ide_bridge(kind, settings)
    return out
