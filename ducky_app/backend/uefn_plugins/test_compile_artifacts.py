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


def test_windows_arm64_is_its_own_build(monkeypatch) -> None:
    from backend.uefn_plugins import manifest_platform as mp

    rec = pa.put_artifact(_record(), pa.artifact_for_build("blender", {"compiled": True, "python_abi": "cp313-win_arm64"}, "r" * 64, 9))
    assert pa.pick_artifact(rec, "windows", "ARM64")["sha256"] == "r" * 64
    assert rec["sha256"] == "w" * 64, "old (x64) apps keep the x64 file on top"
    assert pa.pick_artifact(_record(), "windows", "ARM64") is None
    # Windows x64 asks the Store as it always has; Windows arm64 names its CPU
    monkeypatch.setattr(mp, "host_os", lambda: "windows")
    monkeypatch.setattr(mp, "host_arch", lambda: "x86_64")
    body: dict = {}
    assert pa.ask_for_this_platform(body) is False and body == {}
    monkeypatch.setattr(mp, "host_arch", lambda: "aarch64")
    assert pa.ask_for_this_platform(body) is True and body == {"os": "windows", "arch": "aarch64"}


def test_each_mac_chip_installs_its_own_file(monkeypatch) -> None:
    from backend.uefn_plugins import manifest_platform as mp

    rec = _record()
    for abi, sha in (("cpython-313-darwin-arm64", "m" * 64), ("cpython-313-darwin-x86_64", "i" * 64)):
        rec = pa.put_artifact(rec, pa.artifact_for_build("blender", {"compiled": True, "python_abi": abi}, sha, 8))
    assert len(rec["artifacts"]) == 5 and rec["sha256"] == "w" * 64
    assert pa.pick_artifact(rec, "macos", "arm64")["file"] == "blender-macos-aarch64.zip"
    assert pa.pick_artifact(rec, "macos", "x86_64")["sha256"] == "i" * 64
    assert pa.pick_artifact(_record(), "macos", "arm64") is None
    only_intel = pa.put_artifact({"id": "b", "compiled": True},
                                 pa.artifact_for_build("b", {"compiled": True, "python_abi": "cpython-313-darwin-x86_64"}, "i" * 64, 1))
    assert pa.download_problem({**only_intel, "sha256": "i" * 64}, "macos", "aarch64") == "This plugin has no macOS arm64 build yet"
    monkeypatch.setattr(mp, "host_os", lambda: "macos")
    monkeypatch.setattr(mp, "host_arch", lambda: "aarch64")
    body: dict = {}
    assert pa.ask_for_this_platform(body) is True and body == {"os": "macos", "arch": "aarch64"}


def test_old_client_sees_windows_only() -> None:
    old = pa.legacy_view(_record())
    assert "artifacts" not in old
    assert old["sha256"] == "w" * 64 and old["file"] == "blender-windows-x86_64.zip"
    # a record written before artifacts existed is a Windows build to every app
    legacy = {"id": "b", "compiled": True, "sha256": "o" * 64, "size": 5}
    assert pa.pick_artifact(legacy, "windows", "x86_64")["sha256"] == "o" * 64
    assert pa.pick_artifact(legacy, "linux", "x86_64") is None
    assert pa.legacy_view(legacy) == legacy
