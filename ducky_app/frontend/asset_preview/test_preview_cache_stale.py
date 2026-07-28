"""Stale offline fallback for texture/material image cache (no UEFN required)."""

from __future__ import annotations

import pytest

from frontend.asset_preview import cache as preview_cache
from frontend.asset_preview import service as preview_service


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    root = tmp_path / "UEFN-Ducky"
    monkeypatch.setattr(preview_cache, "resolve_app_data_dir", lambda *, for_write=False: root)
    monkeypatch.setattr(preview_cache, "current_project_cache_slug", lambda: "TestProject_deadbeef")
    yield


def test_latest_pointer_survives_mtime_change():
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        10,
        100,
        b"\x89PNG\r\n\x1a\n",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
        metadata={"size_x": 64, "size_y": 64},
    )
    assert preview_cache.get_cached(rel, 10, 100) is not None
    assert preview_cache.get_cached(rel, 11, 100) is None

    latest = preview_cache.get_latest_cached(rel)
    assert latest is not None
    assert latest.mode == "texture"
    assert latest.asset_class == "Texture2D"
    assert latest.metadata["size_x"] == 64


def test_badge_mode_does_not_clobber_latest_pointer():
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        1,
        1,
        b"real-png",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )
    preview_cache.put_cached(
        rel,
        2,
        1,
        b"badge",
        mode="asset",
        asset_class="",
        asset_path="/Game/Textures/T_Rock",
    )
    latest = preview_cache.get_latest_cached(rel)
    assert latest is not None
    assert latest.mode == "texture"


def test_load_texture_preview_cache_hit_skips_listener(monkeypatch):
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        5,
        20,
        b"\x89PNG",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("listener must not be called on cache hit")

    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 5, "size": 20, "path": path},
    )
    monkeypatch.setattr("backend.bridge.post_command_to_listener", boom)

    out = preview_service.load_texture_preview(rel)
    assert out["ok"] is True
    assert out["from_cache"] is True
    assert out["mode"] == "texture"
    assert called["n"] == 0


def test_load_texture_preview_offline_stale_latest(monkeypatch):
    rel = "Content/Textures/T_Rock.uasset"
    preview_cache.put_cached(
        rel,
        5,
        20,
        b"\x89PNG",
        mode="texture",
        asset_class="Texture2D",
        asset_path="/Game/Textures/T_Rock",
    )

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 99, "size": 20, "path": path},
    )

    out = preview_service.load_texture_preview(rel)
    assert out["ok"] is True
    assert out["stale"] is True
    assert out["from_cache"] is True
    assert out["listener_online"] is False
    assert out["mode"] == "texture"
    assert out["preview_url"]


def test_preview_project_asset_offline_prefers_stale_over_badge(monkeypatch):
    rel = "Content/Materials/M_Glow.uasset"
    preview_cache.put_cached(
        rel,
        1,
        8,
        b"\x89PNG",
        mode="material",
        asset_class="Material",
        asset_path="/Game/Materials/M_Glow",
    )

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda path: {"exists": True, "mtime_ns": 50, "size": 8, "path": path},
    )

    out = preview_service.preview_project_asset(rel)
    assert out["mode"] == "material"
    assert out["stale"] is True
    assert out["listener_online"] is False
    assert out["preview_url"]
