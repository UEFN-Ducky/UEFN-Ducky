"""Service-level tests for on-demand StaticMesh preview (mocked listener)."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.asset_preview import mesh_cache
from frontend.asset_preview import service as preview_service


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    root = tmp_path / "UEFN-Ducky"
    monkeypatch.setattr(mesh_cache, "resolve_app_data_dir", lambda *, for_write=False: root)
    with mesh_cache._lock:  # noqa: SLF001
        mesh_cache._inflight.clear()  # noqa: SLF001
        mesh_cache._inflight_results.clear()  # noqa: SLF001
        mesh_cache._inflight_errors.clear()  # noqa: SLF001
    yield


def test_load_static_mesh_preview_offline(monkeypatch, tmp_path):
    content = tmp_path / "Content" / "Meshes"
    content.mkdir(parents=True)
    asset = content / "SM_Box.uasset"
    asset.write_bytes(b"uasset")

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda rel: {"exists": True, "mtime_ns": 1, "size": 6, "path": rel},
    )

    out = preview_service.load_static_mesh_preview("Content/Meshes/SM_Box.uasset")
    assert out["ok"] is False
    assert "offline" in out["error"].lower()


def test_load_static_mesh_preview_offline_stale_latest(monkeypatch, tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    fbx = export_dir / "model.fbx"
    fbx.write_bytes(b"fbx-bytes")
    mesh_cache.put_cached_mesh(
        "Content/Meshes/SM_Box.uasset",
        10,
        6,
        asset_path="/Game/Meshes/SM_Box",
        exported_file=str(fbx),
        filename="model.fbx",
        metadata={"lod_count": 1},
        siblings_dir=str(export_dir),
    )

    monkeypatch.setattr(preview_service, "_listener_online", lambda: False)
    # Different mtime → exact key miss, latest pointer hit.
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda rel: {"exists": True, "mtime_ns": 99, "size": 6, "path": rel},
    )

    out = preview_service.load_static_mesh_preview("Content/Meshes/SM_Box.uasset")
    assert out["ok"] is True
    assert out["stale"] is True
    assert out["from_cache"] is True
    assert out["listener_online"] is False
    assert "/mesh-previews/" in out["media_url"]


def test_load_static_mesh_preview_cache_hit_skips_listener(monkeypatch, tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    fbx = export_dir / "model.fbx"
    fbx.write_bytes(b"fbx-bytes")
    mesh_cache.put_cached_mesh(
        "Content/Meshes/SM_Box.uasset",
        42,
        6,
        asset_path="/Game/Meshes/SM_Box",
        exported_file=str(fbx),
        filename="model.fbx",
        metadata={"lod_count": 1},
        siblings_dir=str(export_dir),
    )

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("listener must not be called on cache hit")

    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda rel: {"exists": True, "mtime_ns": 42, "size": 6, "path": rel},
    )
    monkeypatch.setattr("backend.bridge.post_command_to_listener", boom)

    out = preview_service.load_static_mesh_preview("Content/Meshes/SM_Box.uasset")
    assert out["ok"] is True
    assert out["from_cache"] is True
    assert called["n"] == 0
    assert "/mesh-previews/" in out["media_url"]


def test_load_static_mesh_preview_exports_once(monkeypatch, tmp_path):
    monkeypatch.setattr(preview_service, "_listener_online", lambda: True)
    monkeypatch.setattr(preview_service, "_content_root", lambda: "/Game/")
    monkeypatch.setattr(
        preview_service.pf,
        "stat_project_file",
        lambda rel: {"exists": True, "mtime_ns": 7, "size": 9, "path": rel},
    )

    def fake_post(port, command, params, timeout=8.0):
        assert command == "preview_static_mesh"
        out_dir = Path(params["output_directory"])
        out_dir.mkdir(parents=True, exist_ok=True)
        fbx = out_dir / "model.fbx"
        fbx.write_bytes(b"exported")
        return {
            "ok": True,
            "asset_path": params["asset_path"],
            "exported_file": str(fbx),
            "output_directory": str(out_dir),
            "filename": "model.fbx",
            "metadata": {"lod_count": 3, "has_nanite": False, "material_slots": 1},
        }

    monkeypatch.setattr("backend.bridge.post_command_to_listener", fake_post)

    first = preview_service.load_static_mesh_preview("Content/Meshes/SM_Box.uasset")
    assert first["ok"] is True
    assert first["from_cache"] is False
    assert first["metadata"]["lod_count"] == 3

    # Second call must be cache hit (fake_post would still work, but from_cache proves skip path).
    second = preview_service.load_static_mesh_preview("Content/Meshes/SM_Box.uasset")
    assert second["ok"] is True
    assert second["from_cache"] is True
