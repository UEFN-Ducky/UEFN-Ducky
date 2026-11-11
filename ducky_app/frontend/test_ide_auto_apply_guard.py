"""A sandboxed instance never rewrites the person's IDE configs on its own.

Oct 16 2026: every boot re-pointed ~/.codex/config.toml, ~/.cursor/mcp.json and
the Claude / Antigravity configs at the running exe, so a sandboxed test build
(own LOCALAPPDATA, panel port 4300) aimed the person's Codex, Claude and Cursor
Ducky tools at a throwaway exe until their own app re-applied.
"""

from __future__ import annotations

import os

import pytest

from frontend import app_paths, ide_apply, ship_newest

windows_only = pytest.mark.skipif(os.name != "nt", reason="known folders are Windows-only")


def _own_profile_env(monkeypatch) -> None:
    """Undo conftest's AppData isolation for this test's environment only (read-only checks)."""
    monkeypatch.setenv("USERPROFILE", app_paths.windows_known_folder(app_paths.FOLDERID_PROFILE))
    monkeypatch.setenv("LOCALAPPDATA", app_paths.windows_known_folder(app_paths.FOLDERID_LOCAL_APP_DATA))


@windows_only
def test_the_users_own_profile_is_not_foreign(monkeypatch):
    _own_profile_env(monkeypatch)
    assert app_paths.foreign_profile_reason() == ""
    monkeypatch.setenv("LOCALAPPDATA", os.environ["LOCALAPPDATA"].upper() + "\\")
    assert app_paths.foreign_profile_reason() == ""


@windows_only
def test_a_sandbox_localappdata_is_foreign(monkeypatch, tmp_path):
    _own_profile_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert "LOCALAPPDATA" in app_paths.foreign_profile_reason()


@windows_only
def test_a_sandbox_home_is_foreign_even_with_a_matching_localappdata(monkeypatch, tmp_path):
    # The shell expands LocalAppData from this process's %USERPROFILE%, so a sandbox
    # home with LOCALAPPDATA under it would look consistent; the profile check catches it.
    _own_profile_env(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    assert "USERPROFILE" in app_paths.foreign_profile_reason()


def test_unknown_profile_folders_count_as_the_users_own(monkeypatch):
    monkeypatch.setattr(app_paths, "windows_known_folder", lambda _folder_id: "")
    assert app_paths.foreign_profile_reason() == ""


def test_a_moved_panel_still_heals_ide_configs(monkeypatch):
    # The person's own app on 4299/4300 must re-point IDEs that an older version left on --port 4300.
    monkeypatch.setattr(app_paths, "foreign_profile_reason", lambda: "")
    monkeypatch.setattr(ide_apply, "PANEL_LISTENER_PORT", 4300)
    assert ide_apply.auto_apply_skip_reason() == ""
    block = ide_apply.build_uefn_server_block(ide_apply.PanelSettings(port=4300))
    assert block["args"][block["args"].index("--port") + 1] == "4200"
    monkeypatch.setattr(app_paths, "foreign_profile_reason", lambda: "LOCALAPPDATA is elsewhere")
    assert ide_apply.auto_apply_skip_reason() == "LOCALAPPDATA is elsewhere"


def _ship(monkeypatch, skip_reason: str) -> tuple[list[str], list[str]]:
    """Run ship_newest_everywhere with every disk / listener step stubbed; return (calls, log)."""
    from backend import bridge
    from backend.skills import seeding, skill_index
    from frontend import deploy, skill_deploy

    calls: list[str] = []
    monkeypatch.setattr(ship_newest, "_last_ship_at", 0.0)
    monkeypatch.setattr(ship_newest, "maybe_bust_conversation_caches_for_app_version", lambda: False)
    monkeypatch.setattr(ship_newest, "_write_ship_stamp", lambda: None)
    monkeypatch.setattr(deploy, "sync_listener_to_appdata", lambda: None)
    monkeypatch.setattr(deploy, "refresh_inits", lambda: [])
    monkeypatch.setattr(bridge, "listener_get_health", lambda *_a, **_k: None)
    monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: skip_reason)
    fake_seed = lambda force=False: calls.append("seed") or []  # noqa: E731
    monkeypatch.setattr(seeding, "seed_skill_packs", fake_seed)
    monkeypatch.setattr(skill_index, "seed_skill_packs", fake_seed)
    monkeypatch.setattr(
        skill_deploy, "sync_skill_all_ides", lambda *_a, **_k: calls.append("ide skills") or []
    )
    monkeypatch.setattr(ide_apply, "apply_all_ide_bridges", lambda *_a, **_k: calls.append("ide bridges") or [])
    log = ship_newest.ship_newest_everywhere(apply_ides=True)
    return calls, log


def test_boot_ship_leaves_ide_configs_alone_when_sandboxed(monkeypatch):
    calls, log = _ship(monkeypatch, "panel port 4300 is not the default 4200")
    assert calls == ["seed"]  # own AppData packs only
    assert "IDE configs and skills left alone: panel port 4300 is not the default 4200" in log


def test_boot_ship_still_applies_for_the_users_own_app(monkeypatch):
    calls, log = _ship(monkeypatch, "")
    assert calls == ["ide skills", "ide bridges"]
    assert not any("left alone" in line for line in log)


def test_gateway_hookup_auto_apply_skips_when_sandboxed(monkeypatch):
    from backend.uefn_plugins.plugin_host import api as api_mod, lifecycle
    from backend.uefn_plugins.plugin_host.testing import patch_host

    queued: list[str] = []
    logs: list[str] = []
    monkeypatch.delenv("UEFN_DUCKY_MCP_BRIDGE", raising=False)
    patch_host(monkeypatch, "_queue_ide_auto_apply", lambda kind, _log: queued.append(kind))
    api = api_mod._PluginApi("cursor")  # type: ignore[attr-defined]
    monkeypatch.setattr(api, "log", logs.append)
    try:
        monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: "LOCALAPPDATA is elsewhere")
        api.register_ide_hookup("cursor", label="Cursor", auto_apply=True)
        assert queued == []
        assert "IDE auto-apply skipped (cursor): LOCALAPPDATA is elsewhere" in logs

        monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: "")
        api.register_ide_hookup("cursor", label="Cursor", auto_apply=True)
        assert queued == ["cursor"]
    finally:
        lifecycle.invalidate_plugin_runtime("cursor")


def test_manual_apply_is_not_guarded(monkeypatch):
    # Settings → Apply / the MCP apply tool write even from a sandbox: the person asked.
    applied: list[str] = []
    monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: "panel port 4300 is not the default 4200")
    monkeypatch.setattr(ide_apply, "_active_ide_kinds", lambda: (ide_apply.IdeKind.CURSOR,))
    monkeypatch.setattr(ide_apply, "apply_ide_bridge", lambda kind, _s=None, **_k: applied.append(kind.value) or "x")
    ide_apply.apply_all_ide_bridges()
    assert applied == ["cursor"]


def test_automatic_skill_syncs_leave_ide_folders_alone_when_sandboxed(monkeypatch):
    # Plugin install/update/uninstall, Store actions and MCP startup all sync through
    # sync_skill_all_ides; a sandbox on port 4300 rewrote ~/.claude/skills that way.
    from backend.skills import seeding, skill_index
    from frontend import skill_deploy

    calls: list[str] = []
    fake_seed = lambda force=False: calls.append("seed") or []  # noqa: E731
    monkeypatch.setattr(seeding, "seed_skill_packs", fake_seed)
    monkeypatch.setattr(skill_index, "seed_skill_packs", fake_seed)
    monkeypatch.setattr(skill_deploy, "sync_skill", lambda **_k: calls.append("ide skills") or [])
    monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: "panel port 4300 is not the default 4200")
    log = skill_deploy.sync_skill_all_ides()
    assert calls == ["seed"]
    assert log == ["IDE skill folders left alone: panel port 4300 is not the default 4200"]

    calls.clear()
    monkeypatch.setattr(ide_apply, "auto_apply_skip_reason", lambda: "")
    skill_deploy.sync_skill_all_ides()
    assert calls == ["ide skills"]
