"""Compiled plugins run on every x86-64 CPU, not only on the PC that built them."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.uefn_plugins import compile as build_engine


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
