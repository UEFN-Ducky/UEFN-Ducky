"""Compiled plugins run on every x86-64 CPU, not only on the PC that built them."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as build_engine


@pytest.fixture(autouse=True)
def _windows_kit(monkeypatch) -> None:
    # These pin the Windows (zig) build; the Linux build is covered by test_compile_linux.py.
    monkeypatch.setattr(build_engine, "IS_LINUX", False)
    monkeypatch.setattr(build_engine, "IS_MACOS", False)


def test_nuitka_compiles_and_links_for_baseline_x86_64(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "uefn_plugin_demo.cp313-win_amd64.pyd").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    build_engine._run_nuitka(Path("python.exe"), tmp_path, "uefn_plugin_demo")
    # Account 1.0.50 linked zig's C runtime for the build PC (AVX-512, BMI2) and
    # crashed Ducky with 0xC000001D on every CPU without them.
    assert seen["CFLAGS"] == "-mcpu=x86_64"
    assert seen["LDFLAGS"] == "-mcpu=x86_64"


def test_each_build_has_its_own_zig_cache(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs.get("env") or {})
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "uefn_plugin_demo.cp313-win_amd64.pyd").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    build_engine._run_nuitka(Path("python.exe"), tmp_path, "uefn_plugin_demo")
    # Forge 1.2.10 failed every build with "failed to link with LLD: FileNotFound" from
    # stale entries in zig's shared cache; a cache inside the work dir starts clean.
    for key in ("ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR"):
        assert Path(seen[key]).is_relative_to(tmp_path)


def test_the_compiler_runs_without_a_console_window(monkeypatch, tmp_path: Path) -> None:
    flags: list[int] = []

    def fake_run(cmd, **kwargs):
        flags.append(kwargs.get("creationflags", 0))
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "uefn_plugin_demo.cp313-win_amd64.pyd").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    build_engine._run_nuitka(Path("python.exe"), tmp_path, "uefn_plugin_demo")
    # From the app (no console) each build step opened its own window, and closing one
    # stopped the build.
    assert flags == [getattr(subprocess, "CREATE_NO_WINDOW", 0)]


def test_nuitka_always_builds_with_zig(monkeypatch, tmp_path: Path) -> None:
    cmds: list[list[str]] = []
    paths: list[str] = []

    def fake_run(cmd, **kwargs):
        cmds.append(list(cmd))
        paths.append((kwargs.get("env") or {}).get("PATH", ""))
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "uefn_plugin_demo.cp313-win_amd64.pyd").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    build_engine._run_nuitka(Path("python.exe"), tmp_path, "uefn_plugin_demo")
    # With Visual Studio installed Nuitka chose MSVC, which ignores -mcpu=x86_64, and the
    # .pyd came out with xgetbv (XSAVE): every Store build on that PC was refused.
    assert "--zig" in cmds[0]
    # the kit's pinned zig comes first on PATH, so Nuitka doesn't fetch another one
    kit_zig = str(Path("python.exe").parent / "Lib" / "site-packages" / "ziglang")
    assert paths[0].split(os.pathsep)[0] == kit_zig


def test_windows_arm64_builds_with_msvc_when_it_is_there(monkeypatch) -> None:
    # zig 0.16's cc crashed (0xC0000005) on Nuitka's __constants.c on windows-11-arm.
    monkeypatch.setattr(build_engine, "WIN_ARM64", True)
    monkeypatch.setattr(build_engine, "arm64_msvc", lambda: True)
    assert build_engine._windows_compiler() == ("--msvc=latest", {})
    monkeypatch.setattr(build_engine, "arm64_msvc", lambda: False)
    assert build_engine._windows_compiler()[0] == "--zig"
    monkeypatch.setattr(build_engine, "WIN_ARM64", False)
    monkeypatch.setattr(build_engine, "arm64_msvc", lambda: True)
    assert build_engine._windows_compiler() == ("--zig", {"CFLAGS": "-mcpu=x86_64", "LDFLAGS": "-mcpu=x86_64"})


def test_arm64_msvc_without_vswhere_is_false(tmp_path: Path) -> None:
    assert build_engine.arm64_msvc(tmp_path / "missing.exe") is False
