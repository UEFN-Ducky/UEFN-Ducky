"""What IDEs are told to launch: the bridge EXE, across one-dir and one-file layouts."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend import mcp_block


def _onedir(dist: Path, version: str, *, bridge: bool = True) -> Path:
    """A one-dir build: dist/UEFN-Ducky-<ver>/{UEFN-Ducky.exe,UEFN-Ducky-Bridge.exe}."""
    folder = dist / f"UEFN-Ducky-{version}"
    folder.mkdir(parents=True)
    (folder / "UEFN-Ducky.exe").write_text("app", encoding="utf-8")
    if bridge:
        (folder / "UEFN-Ducky-Bridge.exe").write_text("bridge", encoding="utf-8")
    return folder


def _onefile(dist: Path, version: str) -> Path:
    """A legacy one-file build: dist/UEFN-Ducky-<ver>.exe."""
    dist.mkdir(parents=True, exist_ok=True)
    exe = dist / f"UEFN-Ducky-{version}.exe"
    exe.write_text("app", encoding="utf-8")
    return exe


@pytest.fixture
def dist(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / "dist").mkdir(parents=True)
    monkeypatch.setattr(mcp_block, "repo_root", lambda: root)
    return root / "dist"


def test_finds_the_exe_inside_a_one_dir_build(dist: Path) -> None:
    folder = _onedir(dist, "1.2.72")
    assert mcp_block.latest_built_exe() == folder / "UEFN-Ducky.exe"


def test_still_finds_a_legacy_one_file_build(dist: Path) -> None:
    exe = _onefile(dist, "1.2.66")
    assert mcp_block.latest_built_exe() == exe


def test_picks_the_newest_version_across_both_layouts(dist: Path) -> None:
    """A half-cleaned dist/ can hold both shapes; version wins, not shape or mtime."""
    _onefile(dist, "1.2.66")
    newer = _onedir(dist, "1.2.72")
    assert mcp_block.latest_built_exe() == newer / "UEFN-Ducky.exe"


def test_version_is_read_from_the_folder_not_the_inner_exe(dist: Path) -> None:
    """One-dir puts the version on the directory — every inner exe is 'UEFN-Ducky.exe'."""
    _onedir(dist, "1.2.9")
    newest = _onedir(dist, "1.2.70")
    assert mcp_block.latest_built_exe() == newest / "UEFN-Ducky.exe", "1.2.70 must beat 1.2.9"


def test_ignores_a_folder_with_no_exe_in_it(dist: Path) -> None:
    (dist / "UEFN-Ducky-9.9.9").mkdir(parents=True)  # e.g. a half-deleted build
    folder = _onedir(dist, "1.2.72")
    assert mcp_block.latest_built_exe() == folder / "UEFN-Ducky.exe"


def test_no_build_at_all_returns_none(dist: Path) -> None:
    assert mcp_block.latest_built_exe() is None


def test_bridge_exe_is_the_sibling_of_the_app_exe(dist: Path) -> None:
    folder = _onedir(dist, "1.2.72")
    assert mcp_block.bridge_exe_for(folder / "UEFN-Ducky.exe") == folder / "UEFN-Ducky-Bridge.exe"


def test_bridge_exe_is_none_when_absent(dist: Path) -> None:
    """A one-file build has no sibling bridge — callers must fall back to the app exe."""
    exe = _onefile(dist, "1.2.66")
    assert mcp_block.bridge_exe_for(exe) is None


def test_packaged_run_points_ides_at_the_bridge(dist: Path, monkeypatch) -> None:
    folder = _onedir(dist, "1.2.72")
    monkeypatch.setattr(mcp_block, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(mcp_block.sys, "executable", str(folder / "UEFN-Ducky.exe"))
    assert mcp_block.resolve_bridge_command() == str(folder / "UEFN-Ducky-Bridge.exe")


def test_packaged_one_file_run_falls_back_to_itself(dist: Path, monkeypatch) -> None:
    """Upgrading from a one-file install must not write a path that does not exist."""
    exe = _onefile(dist, "1.2.66")
    monkeypatch.setattr(mcp_block, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(mcp_block.sys, "executable", str(exe))
    assert mcp_block.resolve_bridge_command() == str(exe.resolve())


def test_dev_run_prefers_the_bridge_in_the_newest_build(dist: Path, monkeypatch) -> None:
    folder = _onedir(dist, "1.2.72")
    monkeypatch.setattr(mcp_block, "is_packaged_runtime", lambda: False)
    assert mcp_block.resolve_bridge_command() == str(folder / "UEFN-Ducky-Bridge.exe")


def test_bridge_command_never_points_at_a_missing_file(dist: Path, monkeypatch) -> None:
    """The whole point: whatever we write into mcp.json must be launchable."""
    _onedir(dist, "1.2.72", bridge=False)  # bridge somehow absent
    monkeypatch.setattr(mcp_block, "is_packaged_runtime", lambda: False)
    assert Path(mcp_block.resolve_bridge_command()).is_file()
