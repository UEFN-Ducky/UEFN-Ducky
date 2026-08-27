"""Push the running build's listener, skills, and IDE MCP bridges everywhere.

Called on panel launch, bridge launch, and when the UEFN listener comes online so a
new EXE / reinstall / reopen always lands the newest bits in AppData + Cursor /
Claude / Antigravity without a manual Settings → Apply.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Callable, Optional

_ship_lock = threading.Lock()
_last_ship_at = 0.0
_MIN_SHIP_INTERVAL_SEC = 5.0
# Cross-process dedupe: every spawned coding agent launches its own bridge exe,
# and each bridge used to redo the full listener/skills/IDE-config ship. Same
# exe + version within this window → skip (stamp lives in AppData).
_STAMP_FRESH_SEC = 10 * 60.0


def _stamp_path():
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True) / "ship_stamp.json"


def _recent_ship_stamp() -> bool:
    try:
        from frontend import __version__

        data = json.loads(_stamp_path().read_text(encoding="utf-8"))
        return (
            str(data.get("version")) == __version__
            and str(data.get("exe")) == str(sys.executable)
            and time.time() - float(data.get("at", 0)) < _STAMP_FRESH_SEC
        )
    except Exception:
        return False


def _write_ship_stamp() -> None:
    try:
        from frontend import __version__

        _stamp_path().write_text(
            json.dumps({"version": __version__, "exe": str(sys.executable), "at": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass


def ship_newest_everywhere(
    *,
    apply_ides: bool = True,
    force_skills: bool = False,
    skip_if_recently_shipped: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Sync listener → AppData, refresh skill packs into every IDE, re-point MCP configs.

    Best-effort: never raises. Safe to call from panel, bridge, or a status poll.

    ``skip_if_recently_shipped`` skips the whole ship when another process of the
    same exe/version stamped a ship recently — spawned coding agents each launch
    their own bridge, and re-shipping per agent is pure disk/CPU churn.

    Preserves personal data: other MCP servers, panel_settings, credentials, chats,
    custom duckies, custom skill packs, and user skill folders (not ``managed_by``).
    Shipped packs upgrade when the bundled version is newer (or app version changes).
    """
    global _last_ship_at
    out: list[str] = []

    def _log(line: str) -> None:
        out.append(line)
        if log is not None:
            log(line)

    if skip_if_recently_shipped and not force_skills and _recent_ship_stamp():
        _log("ship_newest: skipped (same exe/version shipped recently)")
        # Fortnite updates wipe Engine/Plugins — still re-pin init + Toolset boot.
        try:
            from frontend.deploy import refresh_inits

            for line in refresh_inits():
                _log(line)
        except Exception as exc:  # noqa: BLE001
            _log(f"init refresh failed: {exc}")
        return out

    with _ship_lock:
        now = time.time()
        if now - _last_ship_at < _MIN_SHIP_INTERVAL_SEC:
            _log("ship_newest: skipped (recent)")
            try:
                from frontend.deploy import refresh_inits

                for line in refresh_inits():
                    _log(line)
            except Exception as exc:  # noqa: BLE001
                _log(f"init refresh failed: {exc}")
            return out
        _last_ship_at = now

        try:
            from frontend.deploy import sync_listener_to_appdata

            dest = sync_listener_to_appdata()
            _log(f"listener → {dest}" if dest else "listener sync skipped")
        except Exception as exc:  # noqa: BLE001
            _log(f"listener sync failed: {exc}")

        # Always re-assert island + Toolset boot (even if listener tree was already current).
        try:
            from frontend.deploy import refresh_inits

            for line in refresh_inits():
                _log(line)
        except Exception as exc:  # noqa: BLE001
            _log(f"init refresh failed: {exc}")

        # Running listener older than the deployed source? Auto-reload so every
        # panel/bridge open lands on fresh handlers (reload self-heals its tick).
        # A missing source_stamp = pre-stamp build → one migration reload.
        # Fast health-check first: skip ping/discovery (~3s) when the listener is down.
        try:
            from backend.bridge import listener_get_health, send_command
            from frontend.deploy import appdata_listener_dir, listener_tree_stamp
            from frontend.settings import PANEL_LISTENER_PORT

            if listener_get_health(PANEL_LISTENER_PORT, timeout=0.5) is None:
                _log("listener auto-reload: skipped (offline)")
            else:
                disk = listener_tree_stamp(appdata_listener_dir())
                running = str(send_command("ping", timeout=4.0).get("source_stamp") or "")
                if disk and running != disk:
                    send_command("reload_listener", timeout=6.0)
                    _log(f"listener auto-reload: running={running or 'unstamped'} deployed={disk}")
        except Exception:
            pass  # listener offline — nothing to refresh

        try:
            from frontend.settings import PanelSettings
            from frontend.skill_deploy import sync_skill_all_ides

            for ln in sync_skill_all_ides(
                PanelSettings.load().antigravity_config_path,
                force_appdata=force_skills,
            ):
                _log(ln)
        except Exception as exc:  # noqa: BLE001
            _log(f"skill sync failed: {exc}")

        if apply_ides:
            try:
                from frontend.ide_apply import apply_all_ide_bridges

                for ln in apply_all_ide_bridges():
                    _log(ln)
            except Exception as exc:  # noqa: BLE001
                _log(f"IDE apply failed: {exc}")

        _write_ship_stamp()

    return out


def ship_newest_everywhere_async(*, apply_ides: bool = True, force_skills: bool = False) -> None:
    """Fire-and-forget ship on a daemon thread (panel / bridge startup)."""
    threading.Thread(
        target=ship_newest_everywhere,
        kwargs={"apply_ides": apply_ides, "force_skills": force_skills},
        daemon=True,
        name="ship-newest",
    ).start()
