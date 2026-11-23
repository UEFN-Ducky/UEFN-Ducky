"""The Store keeps one file per platform; an install takes its own; old apps see Windows only."""

from __future__ import annotations

from backend.store import plugin_artifacts as pa

WIN = {"compiled": True, "python_abi": "cp313-win_amd64"}
LINUX = {"compiled": True, "python_abi": "cpython-313-x86_64-linux-gnu"}
ARM = {"compiled": True, "python_abi": "cpython-313-aarch64-linux-gnu"}


def _record() -> dict:
    rec = {"id": "blender", "version": "2.0.0", "compiled": True}
    for manifest, sha in ((WIN, "w" * 64), (LINUX, "l" * 64), (ARM, "a" * 64)):
        rec = pa.put_artifact(rec, pa.artifact_for_build("blender", manifest, sha, 10))
    return rec


def test_each_platform_installs_its_own_file() -> None:
    rec = _record()
    assert pa.pick_artifact(rec, "linux", "x86_64")["file"] == "blender-linux-x86_64.zip"
    assert pa.pick_artifact(rec, "linux", "arm64")["file"] == "blender-linux-aarch64.zip"
    assert pa.pick_artifact(rec, "windows", "AMD64")["sha256"] == "w" * 64


def test_one_file_per_platform_a_rebuild_replaces_it() -> None:
    rec = pa.put_artifact(_record(), pa.artifact_for_build("blender", LINUX, "n" * 64, 11))
    linux = [a for a in rec["artifacts"] if a["os"] == "linux" and a["arch"] == "x86_64"]
    assert [a["sha256"] for a in linux] == ["n" * 64]
    assert len(rec["artifacts"]) == 3


def test_windows_only_version_has_nothing_for_linux() -> None:
    rec = pa.put_artifact({"id": "x", "compiled": True}, pa.artifact_for_build("x", WIN, "w" * 64, 1))
    assert pa.pick_artifact(rec, "linux", "x86_64") is None


def test_source_plugin_installs_everywhere() -> None:
    rec = pa.put_artifact({"id": "vim"}, pa.artifact_for_build("vim", {"id": "vim"}, "s" * 64, 1))
    assert pa.pick_artifact(rec, "linux", "aarch64")["file"] == "vim.zip"
    assert pa.pick_artifact(rec, "windows", "x86_64")["file"] == "vim.zip"


def test_old_client_sees_windows_only() -> None:
    old = pa.legacy_view(_record())
    assert "artifacts" not in old
    assert old["sha256"] == "w" * 64 and old["file"] == "blender-windows-x86_64.zip"
    # a record written before artifacts existed is a Windows build to every app
    legacy = {"id": "b", "compiled": True, "sha256": "o" * 64, "size": 5}
    assert pa.pick_artifact(legacy, "windows", "x86_64")["sha256"] == "o" * 64
    assert pa.pick_artifact(legacy, "linux", "x86_64") is None
    assert pa.legacy_view(legacy) == legacy
