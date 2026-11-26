"""macOS plugin builds: the pinned kit per chip, Xcode clang with baseline flags, ad-hoc signing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as build_engine


def test_mac_kit_sources_are_pinned_per_cpu() -> None:
    for arch in ("x86_64", "aarch64"):
        assert build_engine.pbs_python_url(arch, macos=True).endswith(
            f"-{arch}-apple-darwin-install_only.tar.gz")
        assert len(build_engine.PBS_SHA256_MACOS[arch]) == 64
        assert len(build_engine.ESBUILD_MACOS[arch][1]) == 64
    assert build_engine.PBS_SHA256_MACOS["x86_64"] != build_engine.PBS_SHA256["x86_64"]
    assert build_engine.esbuild_linux_url("aarch64", macos=True).endswith("darwin-arm64-0.28.2.tgz")
    assert build_engine.esbuild_linux_url("x86_64", macos=True).endswith("darwin-x64-0.28.2.tgz")


@pytest.fixture
def as_mac(monkeypatch):
    monkeypatch.setattr(build_engine, "IS_LINUX", False)
    monkeypatch.setattr(build_engine, "IS_MACOS", True)
    monkeypatch.setattr(build_engine, "LINUX_ARCH", "aarch64", raising=False)
    monkeypatch.setattr(build_engine.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("CC", raising=False)


@pytest.mark.parametrize("arch,flags", [("x86_64", "-arch x86_64 -march=x86-64"), ("aarch64", "-arch arm64")])
def test_mac_nuitka_uses_xcode_clang_and_signs(monkeypatch, tmp_path: Path, as_mac, arch, flags) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs.get("env") or {}))
        if cmd[0] == "xcode-select":
            return subprocess.CompletedProcess(cmd, 0, "/Applications/Xcode.app/Contents/Developer\n", "")
        if "nuitka" in cmd:
            (tmp_path / "out").mkdir(exist_ok=True)
            (tmp_path / "out" / "demo.cpython-313-darwin.so").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine, "LINUX_ARCH", arch, raising=False)
    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    out, _ = build_engine._run_nuitka(Path("python3"), tmp_path, "demo")
    assert out.name == "demo.cpython-313-darwin.so"
    nuitka = next(env for cmd, env in calls if "nuitka" in cmd)
    assert "--zig" not in next(cmd for cmd, _ in calls if "nuitka" in cmd)
    assert nuitka["CC"] == "clang" and nuitka["CFLAGS"] == flags == nuitka["LDFLAGS"]
    assert nuitka["MACOSX_DEPLOYMENT_TARGET"] == "13.0"
    assert calls[-1][0] == ["codesign", "--force", "--sign", "-", str(out)]


def test_developer_id_signs_when_the_runner_has_it(monkeypatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(build_engine.subprocess, "run",
                        lambda cmd, **kw: seen.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setenv("DUCKY_CODESIGN_IDENTITY", "Developer ID Application")
    monkeypatch.setenv("DUCKY_CODESIGN_KEYCHAIN", "/tmp/k.keychain-db")
    build_engine.codesign_module(tmp_path / "m.so")
    assert seen == [["codesign", "--force", "--sign", "Developer ID Application", "--timestamp",
                     "--keychain", "/tmp/k.keychain-db", str(tmp_path / "m.so")]]


def test_mac_without_xcode_says_how_to_get_it(monkeypatch, as_mac) -> None:
    monkeypatch.setattr(build_engine.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, "", "no developer tools"))
    with pytest.raises(build_engine.CompileError, match="xcode-select --install"):
        build_engine._xcode_clang()


def test_mac_kit_lives_where_the_linux_one_does(as_mac, tmp_path: Path) -> None:
    assert build_engine._kit_python(tmp_path) == tmp_path / "python" / "bin" / "python3"
    assert build_engine._kit_esbuild(tmp_path) == tmp_path / "esbuild" / "esbuild"
    assert "|macos|" in build_engine._kit_marker_value()
