"""Tests for bounded StaticMesh preview cache (no UEFN required)."""

from __future__ import annotations

import threading
import time

import pytest

from frontend.asset_preview import mesh_cache


@pytest.fixture(autouse=True)
def _isolate_mesh_cache(tmp_path, monkeypatch):
    root = tmp_path / "UEFN-Ducky"
    monkeypatch.setattr(mesh_cache, "resolve_app_data_dir", lambda *, for_write=False: root)
    monkeypatch.setattr(mesh_cache, "current_project_cache_slug", lambda: "TestProject_deadbeef")
    # Reset inflight state between tests.
    with mesh_cache._lock:  # noqa: SLF001
        mesh_cache._inflight.clear()  # noqa: SLF001
        mesh_cache._inflight_results.clear()  # noqa: SLF001
        mesh_cache._inflight_errors.clear()  # noqa: SLF001
    yield


def test_cache_key_changes_with_mtime_and_size():
    a = mesh_cache.cache_key("Content/Meshes/SM_Box.uasset", 1, 10)
    b = mesh_cache.cache_key("Content/Meshes/SM_Box.uasset", 2, 10)
    c = mesh_cache.cache_key("Content/Meshes/SM_Box.uasset", 1, 11)
    d = mesh_cache.cache_key("Content/Meshes/SM_Other.uasset", 1, 10)
    assert a != b != c
    assert a != d
    assert len(a) == 64


def test_put_and_get_cached_mesh(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    fbx = export_dir / "model.fbx"
    fbx.write_bytes(b"Kaydara FBX Binary  \x00")
    (export_dir / "diffuse.png").write_bytes(b"\x89PNG\r\n")

    record = mesh_cache.put_cached_mesh(
        "Content/Meshes/SM_Box.uasset",
        100,
        50,
        asset_path="/Game/Meshes/SM_Box",
        exported_file=str(fbx),
        filename="model.fbx",
        metadata={"lod_count": 2, "has_nanite": False},
        siblings_dir=str(export_dir),
    )
    assert record.from_cache is False
    assert "mesh-previews/" in record.media_url
    assert record.media_filename == "model.fbx"

    hit = mesh_cache.get_cached_mesh("Content/Meshes/SM_Box.uasset", 100, 50)
    assert hit is not None
    assert hit.from_cache is True
    assert hit.metadata["lod_count"] == 2
    assert (mesh_cache.cache_dir_for_id(hit.cache_id) / "diffuse.png").is_file()

    miss = mesh_cache.get_cached_mesh("Content/Meshes/SM_Box.uasset", 101, 50)
    assert miss is None

    # Latest pointer survives mtime change.
    latest = mesh_cache.get_latest_cached_mesh("Content/Meshes/SM_Box.uasset")
    assert latest is not None
    assert latest.cache_id == record.cache_id
    assert latest.metadata["lod_count"] == 2


def test_latest_mesh_pointer_dangling_is_miss(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    fbx = export_dir / "model.fbx"
    fbx.write_bytes(b"fbx")
    record = mesh_cache.put_cached_mesh(
        "Content/Meshes/SM_Box.uasset",
        1,
        1,
        asset_path="/Game/Meshes/SM_Box",
        exported_file=str(fbx),
        filename="model.fbx",
        siblings_dir=str(export_dir),
    )
    import shutil

    shutil.rmtree(mesh_cache.cache_dir_for_id(record.cache_id))
    assert mesh_cache.get_latest_cached_mesh("Content/Meshes/SM_Box.uasset") is None


def test_resolve_mesh_preview_path_rejects_escape(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    fbx = export_dir / "model.fbx"
    fbx.write_bytes(b"fbx")
    record = mesh_cache.put_cached_mesh(
        "Content/Meshes/SM_Box.uasset",
        1,
        1,
        asset_path="/Game/Meshes/SM_Box",
        exported_file=str(fbx),
        filename="model.fbx",
        siblings_dir=str(export_dir),
    )
    ok = mesh_cache.resolve_mesh_preview_path(record.cache_id, "model.fbx")
    assert ok.name == "model.fbx"
    with pytest.raises(ValueError):
        mesh_cache.resolve_mesh_preview_path(record.cache_id, "../evil.fbx")
    with pytest.raises(ValueError):
        mesh_cache.resolve_mesh_preview_path("not-a-valid-id", "model.fbx")


def test_prune_mesh_cache_keeps_newest():
    for i in range(5):
        export_dir = mesh_cache.mesh_cache_root(for_write=True) / f"src{i}"
        export_dir.mkdir(parents=True, exist_ok=True)
        fbx = export_dir / "model.fbx"
        fbx.write_bytes(b"fbx")
        mesh_cache.put_cached_mesh(
            f"Content/Meshes/SM_{i}.uasset",
            i,
            1,
            asset_path=f"/Game/Meshes/SM_{i}",
            exported_file=str(fbx),
            filename="model.fbx",
            siblings_dir=str(export_dir),
        )
        time.sleep(0.02)
    removed = mesh_cache.prune_mesh_cache(max_entries=2)
    assert removed >= 3
    remaining = [p for p in mesh_cache.mesh_cache_root().iterdir() if p.is_dir() and len(p.name) == 64]
    assert len(remaining) == 2


def test_inflight_dedup_waits_for_leader():
    cache_id = mesh_cache.cache_key("Content/Meshes/SM_Box.uasset", 9, 9)
    is_leader, event = mesh_cache.begin_inflight(cache_id)
    assert is_leader is True

    follower_results: list[dict] = []

    def follower():
        is_l2, ev2 = mesh_cache.begin_inflight(cache_id)
        assert is_l2 is False
        follower_results.append(mesh_cache.wait_inflight(cache_id, ev2, timeout=2.0))

    t = threading.Thread(target=follower)
    t.start()
    time.sleep(0.05)
    mesh_cache.finish_inflight(cache_id, result={"ok": True, "from_cache": False})
    t.join(timeout=2.0)
    assert follower_results == [{"ok": True, "from_cache": False}]
