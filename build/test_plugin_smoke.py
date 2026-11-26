"""plugin_smoke: which Store plugins are installed, skipped or failed on a Mac."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plugin_smoke as smoke_mod  # noqa: E402

NO_MAC = {"slug": "vim", "platforms": ["windows-x86_64", "linux-x86_64", "linux-aarch64"]}
SOURCE = {"slug": "kanban", "platforms": None}
ARM_ONLY = {"slug": "artstudio", "platforms": ["windows-x86_64", "macos-aarch64"]}
BOTH = {"slug": "tripo", "platforms": ["macos-aarch64", "macos-x86_64"]}


def test_skip_reason():
    assert smoke_mod.skip_reason(NO_MAC, "macos", "aarch64") == "skipped: no macOS build"
    assert smoke_mod.skip_reason(SOURCE, "macos", "aarch64") is None
    assert smoke_mod.skip_reason(None, "macos", "aarch64") is None
    assert smoke_mod.skip_reason(ARM_ONLY, "macos", "aarch64") is None
    assert smoke_mod.skip_reason(ARM_ONLY, "macos", "x86_64") == "skipped: no macOS x86_64 build"
    assert smoke_mod.skip_reason(BOTH, "macos", "x86_64") is None


def test_smoke_classifies_ok_skip_fail():
    records = {r["slug"]: r for r in (NO_MAC, SOURCE, ARM_ONLY, BOTH)}
    installed: list[str] = []

    def register(pid):
        if pid == "tripo":
            raise ModuleNotFoundError("No module named 'winreg'")

    results = smoke_mod.smoke(["vim", "kanban", "artstudio", "tripo"], installed.append,
                              records, register=register, where=("macos", "aarch64"))
    assert installed == ["kanban", "artstudio", "tripo"]  # vim never installs
    assert results["vim"] == "skipped: no macOS build"
    assert results["kanban"] == results["artstudio"] == "ok"
    assert results["tripo"].startswith("ModuleNotFoundError")
    _line, failed = smoke_mod.summary(results)
    assert failed == 1


def test_skips_alone_pass():
    results = {"a": "ok", "b": "skipped: no macOS build", "c": "skipped: no macOS x86_64 build"}
    line, failed = smoke_mod.summary(results)
    assert failed == 0
    assert line.startswith("1 ok · 2 skipped (no ") and line.endswith(" build) · 0 failed")
