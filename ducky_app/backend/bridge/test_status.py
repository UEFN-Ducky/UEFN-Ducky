"""Tests for GET-health-authoritative listener online / wedged status."""

from __future__ import annotations

from backend.bridge.status import ListenerStatusState, fetch_listener_status


def _patch_epic(monkeypatch, *, online: bool = False) -> None:
    monkeypatch.setattr(
        "backend.mcp_plugins.epic.probe_epic_mcp",
        lambda **_k: {
            "epic_mcp_online": online,
            "epic_mcp_reason": "" if online else "unreachable",
            "epic_mcp_url": "http://127.0.0.1:8000/mcp",
            "epic_mcp_setup_steps": [] if online else ["enable Auto Start"],
        },
    )


def test_busy_listener_stays_online(monkeypatch):
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {
            "status": "ok",
            "busy": True,
            "uptime_sec": 12,
            "tick_age_sec": 0.1,
            "current_command": "spawn_actor",
        },
    )

    def _fail_ping(*_a, **_k):
        raise AssertionError("status must never POST ping")

    monkeypatch.setattr("backend.bridge.status.ping_listener", _fail_ping)
    monkeypatch.setattr(
        "backend.bridge.status.post_command_to_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST from status")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["busy"] is True
    assert "busy" in status["status_text"].lower()
    assert status["current_command"] == "spawn_actor"


def test_stale_tick_wedges_after_streak(monkeypatch):
    _patch_epic(monkeypatch)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 30, "tick_age_sec": 25.0},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )
    state = ListenerStatusState()

    first = fetch_listener_status(4200, state=state, version="test")
    assert first["online"] is True
    assert first["wedged"] is False

    second = fetch_listener_status(4200, state=state, version="test")
    assert second["online"] is True
    assert second["wedged"] is True
    assert "wedged" in second["status_text"].lower()


def test_get_failure_is_offline(monkeypatch):
    _patch_epic(monkeypatch)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no ping when GET fails")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False
    assert status["wedged"] is False
    assert status["status_text"].startswith("Offline")


def test_fresh_tick_is_online(monkeypatch):
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {
            "status": "ok",
            "busy": False,
            "uptime_sec": 90,
            "tick_age_sec": 0.05,
            "project_name": "MyIsland",
            "project_dir": "C:/proj",
        },
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["uptime_sec"] == 90.0
    assert status["uefn_project_name"] == "MyIsland"


def test_legacy_listener_without_tick_age_stays_online(monkeypatch):
    """Old listeners lack tick_age_sec — do not POST; stay online without wedging."""
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {"status": "ok", "busy": False, "uptime_sec": 45},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )
    monkeypatch.setattr("backend.bridge.status.seconds_since_last_post_ok", lambda: 2.0)

    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is True
    assert status["wedged"] is False
    assert status["epic_mcp_online"] is True
    assert "UEFN MCP online" in status["status_text"]


def test_epic_mcp_offline_includes_setup_steps(monkeypatch):
    _patch_epic(monkeypatch, online=False)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test")
    assert status["online"] is False
    assert status["epic_mcp_online"] is False
    assert status["epic_mcp_reason"] == "unreachable"
    assert status["epic_mcp_setup_steps"]
    assert "UEFN MCP offline" in status["status_text"]


def test_unreal_project_status_names_unreal_editor(monkeypatch, tmp_path):
    # A folder with a .uproject never uses the UEFN listener; its editor is Unreal Editor.
    game = tmp_path / "MyGame"
    (game / "Content").mkdir(parents=True)
    (game / "MyGame.uproject").write_text("{}", encoding="utf-8")
    _patch_epic(monkeypatch, online=False)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    status = fetch_listener_status(4200, state=ListenerStatusState(), version="test",
                                   selected_project_root=str(game))
    assert status["status_text"] == "Unreal project · Unreal Editor MCP offline"


def test_island_sweep_runs_once_a_minute_whatever_state_the_caller_passes(monkeypatch, tmp_path):
    # Oct 10 2026: every HTTP poll, agent turn and coding-agent run asked with a fresh (or
    # no) state, so the once-a-minute island sweep ran every time and rewrote a file in the
    # Fortnite install about every 8 s.
    from backend.bridge import status as status_mod
    from frontend import deploy, project_kind

    _patch_epic(monkeypatch)
    monkeypatch.setattr("backend.bridge.status.listener_get_health", lambda _port: None)
    monkeypatch.setattr(status_mod, "_python_sweep_at", {}, raising=False)
    sweeps: list[str] = []
    monkeypatch.setattr(deploy, "resolve_uefn_project_root", lambda root: root)
    monkeypatch.setattr(project_kind, "has_uefnproject", lambda _root: True)
    monkeypatch.setattr(deploy, "refresh_inits", lambda root: sweeps.append(str(root)))
    island = str(tmp_path / "Island")

    for i in range(20):
        state = ListenerStatusState() if i % 2 else None
        fetch_listener_status(4200, state=state, version="test", selected_project_root=island)
    assert len(sweeps) == 1

    other = str(tmp_path / "Other")
    fetch_listener_status(4200, version="test", selected_project_root=other)
    assert len(sweeps) == 2  # a newly selected island gets its own sweep at once


# --------------------------------------------------------------------------- project match
#
# UEFN names the live island by its content mount — the ROOT PLUGIN name, which it
# makes a module GUID for some islands (folder MyProjectBJJJ, mount 86ef2167-…). The
# folder name alone must never decide project_match.

GUID = "86ef2167-1111-2222-3333-444444444444"


def _flat_island(tmp_path, name="MyProjectBJJJ", *, plugin=GUID, title=None, manifest=None):
    root = tmp_path / name
    (root / "Content" / "Verse").mkdir(parents=True)
    if manifest is None:
        data = {"title": title or name, "plugins": [{"name": plugin, "bIsRoot": True, "bIsPublic": False}]}
        manifest = __import__("json").dumps(data)
    (root / f"{name}.uefnproject").write_text(manifest, encoding="utf-8")
    (root / f"{plugin}.uplugin").write_text("{}", encoding="utf-8")
    return root


def _old_layout_island(tmp_path, name="Dark", *, plugin="Dark"):
    """Pre-37 layout: the plugin and its Content live under Plugins/<Plugin>/."""
    root = tmp_path / name
    (root / "Plugins" / plugin / "Content" / "Verse").mkdir(parents=True)
    (root / f"{name}.uefnproject").write_text("{}", encoding="utf-8")  # no plugins[] at all
    (root / "Plugins" / plugin / f"{plugin}.uplugin").write_text("{}", encoding="utf-8")
    return root


def _health(name, **extra):
    return {"status": "ok", "project_name": name, "project_dir": "../../../FortniteGame/", **extra}


def _match(health, selected):
    from backend.bridge.status import _project_from_health
    from frontend.project_kind import forget_cached_identity, forget_cached_kind

    forget_cached_kind()
    forget_cached_identity()
    return _project_from_health(health, selected_project_root=str(selected))


def test_guid_mount_matches_the_selected_island(tmp_path):
    island = _flat_island(tmp_path)
    _dir, name, match = _match(_health(GUID), island)
    assert name == GUID
    assert match is True


def test_guid_mount_matches_case_insensitively(tmp_path):
    island = _flat_island(tmp_path)
    assert _match(_health(GUID.upper()), island)[2] is True


def test_other_island_open_is_still_a_mismatch(tmp_path):
    island = _flat_island(tmp_path)
    assert _match(_health("Tycoony"), island)[2] is False
    assert _match(_health("aaaaaaaa-0000-0000-0000-000000000000"), island)[2] is False


def test_title_and_folder_name_also_identify_the_island(tmp_path):
    island = _flat_island(tmp_path, title="My Project BJJJ")
    assert _match(_health("MyProjectBJJJ"), island)[2] is True
    assert _match(_health("My Project BJJJ"), island)[2] is True


def test_old_plugins_layout_matches_by_uplugin_stem(tmp_path):
    island = _old_layout_island(tmp_path, plugin="DarkPlugin")
    assert _match(_health("DarkPlugin"), island)[2] is True
    assert _match(_health("Dark"), island)[2] is True  # the folder name still counts


def test_selected_content_folder_climbs_to_the_island(tmp_path):
    island = _flat_island(tmp_path)
    assert _match(_health(GUID), island / "Content")[2] is True


def test_malformed_manifest_falls_back_to_folder_name(tmp_path):
    island = _flat_island(tmp_path, manifest="{not json")
    assert _match(_health("MyProjectBJJJ"), island)[2] is True
    # The uplugin stem next to it still names the mount.
    assert _match(_health(GUID), island)[2] is True


def test_reserved_mount_means_no_island_open(tmp_path):
    island = _flat_island(tmp_path)
    for mount in ("Temp", "Game", "Engine", "Script"):
        _dir, name, match = _match(_health(mount), island)
        assert name == ""
        assert match is True


def test_island_dir_from_health_matches_by_path(tmp_path):
    """A newer listener reports the island folder: path wins even for an unknown mount."""
    island = _flat_island(tmp_path)
    health = _health("something-unrelated", island_dir=str(island))
    assert _match(health, island)[2] is True
    other = _flat_island(tmp_path, name="Other", plugin="Other")
    assert _match(_health("something-unrelated", island_dir=str(other)), island)[2] is False


def test_folder_project_never_mismatches(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    assert _match(_health(GUID), repo)[2] is True


def test_project_fields_cache_is_per_selected_root(tmp_path, monkeypatch):
    from backend.bridge import status as status_mod
    from frontend.project_kind import forget_cached_identity, forget_cached_kind

    forget_cached_kind()
    forget_cached_identity()
    island = _flat_island(tmp_path)
    other = _flat_island(tmp_path, name="Other", plugin="Other")
    monkeypatch.setattr(status_mod, "listener_get_health", lambda _port: _health(GUID))
    _d, _n, match, cache = status_mod.listener_project_fields(4200, selected_project_root=str(island))
    assert match is True
    # Same cache handed back for a different project must not answer for the old one.
    _d, _n, match2, cache2 = status_mod.listener_project_fields(
        4200, selected_project_root=str(other), cache=cache
    )
    assert match2 is False
    assert cache2["selected_root"] != cache["selected_root"]


def test_status_reports_both_sides_of_the_match(tmp_path, monkeypatch):
    from frontend.project_kind import forget_cached_identity, forget_cached_kind

    forget_cached_kind()
    forget_cached_identity()
    island = _flat_island(tmp_path)
    _patch_epic(monkeypatch, online=True)
    monkeypatch.setattr(
        "backend.bridge.status.listener_get_health",
        lambda _port: {**_health(GUID), "busy": False, "uptime_sec": 9, "tick_age_sec": 0.1},
    )
    monkeypatch.setattr(
        "backend.bridge.status.ping_listener",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no POST ping")),
    )
    status = fetch_listener_status(
        4200, state=ListenerStatusState(), version="test", selected_project_root=str(island)
    )
    assert status["uefn_project_name"] == GUID
    assert status["project_match"] is True
    assert status["selected_project_mount"] == GUID


def test_project_switch_forgets_cached_island(tmp_path, monkeypatch):
    from backend.bridge import status as status_mod

    st = ListenerStatusState()
    st.project_cache = {"at": 1.0, "selected_root": "x", "uefn_project_name": "Old", "project_match": False}
    st.ping_fail_streak = 3
    status_mod.forget_listener_project_caches()
    assert st.project_cache is None
    assert st.ping_fail_streak == 0
