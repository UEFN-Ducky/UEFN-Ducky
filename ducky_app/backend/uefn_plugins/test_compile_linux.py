"""Linux plugin builds: per-target ABI, the pinned Linux kit, and a real .so compiled and imported."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from importlib.machinery import ExtensionFileLoader
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as build_engine
from backend.uefn_plugins import manifest_platform as mp


def test_abi_per_target() -> None:
    assert mp.python_abi("windows", "x86_64", (3, 13)) == "cp313-win_amd64"
    assert mp.python_abi("linux", "x86_64", (3, 12)) == "cpython-312-x86_64-linux-gnu"
    assert mp.python_abi("linux", "arm64", (3, 12)) == "cpython-312-aarch64-linux-gnu"
    assert mp.module_suffix("linux") == ".so" and mp.module_suffix("windows") == ".pyd"
    with pytest.raises(ValueError):
        mp.python_abi("darwin", "arm64")


def test_windows_abi_is_unchanged_on_windows() -> None:
    if sys.platform == "win32":
        assert build_engine.PY_ABI == f"cp{sys.version_info[0]}{sys.version_info[1]}-win_amd64"
        assert build_engine.MODULE_SUFFIX == ".pyd"


def test_linux_kit_sources_are_pinned_per_cpu() -> None:
    for arch in ("x86_64", "aarch64"):
        assert f"-{arch}-unknown-linux-gnu-install_only.tar.gz" in build_engine.pbs_python_url(arch)
        assert len(build_engine.PBS_SHA256[arch]) == 64
        assert len(build_engine.ESBUILD_LINUX[arch][1]) == 64
    assert build_engine.esbuild_linux_url("aarch64").endswith("linux-arm64-0.28.2.tgz")


def test_linux_nuitka_uses_baseline_flags(monkeypatch, tmp_path: Path) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["env"] = list(cmd), kwargs.get("env") or {}
        (tmp_path / "out").mkdir(exist_ok=True)
        (tmp_path / "out" / "demo.cpython-313-x86_64-linux-gnu.so").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(build_engine, "IS_LINUX", True)
    monkeypatch.setattr(build_engine, "LINUX_ARCH", "x86_64", raising=False)
    monkeypatch.setenv("CC", "gcc")
    monkeypatch.setattr(build_engine.subprocess, "run", fake_run)
    out, _ = build_engine._run_nuitka(Path("python3"), tmp_path, "demo")
    assert out.name == "demo.cpython-313-x86_64-linux-gnu.so"
    assert "--zig" not in seen["cmd"]
    assert seen["env"]["CFLAGS"] == "-march=x86-64 -mtune=generic"


_REAL = (
    sys.platform.startswith("linux")
    and sys.version_info[:2] == tuple(int(p) for p in build_engine.PBS_PYTHON_VERSION.split(".")[:2])
    and bool(os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc"))
    and os.environ.get("DUCKY_SKIP_BUILD_KIT_TEST") != "1"
)


@pytest.mark.skipif(not _REAL, reason="real kit build: Linux, Python 3.13 and a C compiler")
def test_compiles_and_imports_a_linux_module(monkeypatch, tmp_path_factory) -> None:
    kit_home = Path(os.environ.get("DUCKY_BUILD_KIT_DIR") or tmp_path_factory.mktemp("kit")) / "build_kit"
    monkeypatch.setattr(build_engine, "build_kit_dir", lambda: kit_home)
    kit = build_engine.ensure_build_kit()
    kpy = build_engine._kit_python(kit)
    build_engine._check_kit_abi(kpy)

    work = tmp_path_factory.mktemp("work")
    (work / "hello").mkdir()
    (work / "hello" / "__init__.py").write_text("def register():\n    return 'hello'\n", encoding="utf-8")
    so, _ = build_engine._run_nuitka(kpy, work, "hello")
    assert so.name == f"hello.{build_engine.PY_ABI}.so"

    spec = importlib.util.spec_from_file_location("hello", so, loader=ExtensionFileLoader("hello", str(so)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.register() == "hello"
