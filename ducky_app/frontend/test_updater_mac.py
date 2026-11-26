"""UEFN Ducky.app self-update: pick the .app.zip, verify, swap, roll back."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from frontend import updater

FEED = {"assets": [
    {"os": "macos", "arch": "arm64", "package": "app-zip", "url": "https://x/UEFN-Ducky-2.31.1-macos-arm64.app.zip", "sha256": "a"},
    {"os": "macos", "arch": "x64", "package": "app-zip", "url": "https://x/UEFN-Ducky-2.31.1-macos-x64.app.zip", "sha256": "b"},
    {"os": "macos", "arch": "arm64", "package": "dmg", "url": "https://x/UEFN-Ducky-2.31.1-macos-arm64.dmg", "sha256": "c"},
]}


class FakeMac:
    """codesign / spctl / ditto stand-ins. ``ditto -x`` unpacks a new app with ``marker``."""

    def __init__(self, *, signed: bool = False, verify_rc: int = 0, spctl_rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.signed, self.verify_rc, self.spctl_rc = signed, verify_rc, spctl_rc

    def __call__(self, cmd):
        self.calls.append([str(c) for c in cmd])
        rc, err = 0, ""
        if cmd[:2] == ["codesign", "-dv"]:
            err = "Authority=Developer ID Application: Ducky (ABCDE12345)\n" if self.signed else "Signature=adhoc\n"
        elif cmd[:2] == ["codesign", "--verify"]:
            rc = self.verify_rc
        elif cmd[0] == "spctl":
            rc = self.spctl_rc
        elif cmd[:2] == ["ditto", "-x"]:
            new = Path(cmd[-1]) / "UEFN Ducky.app" / "Contents"
            new.mkdir(parents=True)
            (new / "Info.plist").write_text("new", encoding="utf-8")
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": err})()

    def tools(self) -> list[str]:
        return [c[0] if c[0] != "codesign" else " ".join(c[:2]) for c in self.calls]


@pytest.fixture
def swaps(monkeypatch):
    from frontend import app_versions

    made = []
    monkeypatch.setattr(app_versions, "begin_appimage_swap", lambda version, **kw: made.append((version, kw)))
    return made


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "Applications" / "UEFN Ducky.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text("old", encoding="utf-8")
    return app


def _archive(tmp_path: Path) -> tuple[Path, str]:
    archive = tmp_path / "Applications" / ".UEFN Ducky.app.zip"
    archive.write_bytes(b"zip")
    return archive, hashlib.sha256(b"zip").hexdigest()


def test_pick_the_app_zip_for_this_mac() -> None:
    assert updater.pick_asset(FEED, plat="darwin", machine="arm64", kind="app")["sha256"] == "a"
    assert updater.pick_asset(FEED, plat="darwin", machine="x86_64", kind="app")["sha256"] == "b"
    assert updater.UPDATE_ACTIONS["app"] == "restart"


def test_the_running_bundle_is_found_from_its_executable(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert updater.mac_app_bundle(app / "Contents" / "MacOS" / "UEFN-Ducky") == app.resolve()
    assert updater.mac_app_bundle(tmp_path / "python") is None


def test_adhoc_update_is_verified_and_swapped(tmp_path: Path, swaps, capsys) -> None:
    app, run = _app(tmp_path), FakeMac()
    archive, sha = _archive(tmp_path)
    assert updater.install_mac_app(app, archive, sha256=sha, version="2.31.1", run=run) is None
    assert (app / "Contents" / "Info.plist").read_text(encoding="utf-8") == "new"
    assert (app.with_name("UEFN Ducky.app.prev") / "Contents" / "Info.plist").read_text(encoding="utf-8") == "old"
    assert run.tools() == ["ditto", "codesign -dv", "codesign --verify"]  # ad-hoc: no spctl
    assert ["codesign", "--verify", "--strict", str(app.with_name(".UEFN Ducky.app.new") / "UEFN Ducky.app")] in run.calls
    assert not archive.exists() and not app.with_name(".UEFN Ducky.app.new").exists()
    assert swaps[0][0] == "2.31.1" and swaps[0][1]["previous"] == app.with_name("UEFN Ducky.app.prev")
    assert "verified (adhoc), swapped" in capsys.readouterr().out


def test_developer_id_app_also_needs_gatekeeper(tmp_path: Path, swaps) -> None:
    app, run = _app(tmp_path), FakeMac(signed=True)
    archive, sha = _archive(tmp_path)
    assert updater.install_mac_app(app, archive, sha256=sha, version="2.31.1", run=run) is None
    assert run.tools()[-1] == "spctl"
    app2 = _app(tmp_path / "b")
    archive2, sha2 = _archive(tmp_path / "b")
    error = updater.install_mac_app(app2, archive2, sha256=sha2, version="2.31.1", run=FakeMac(signed=True, spctl_rc=3))
    assert "Gatekeeper" in error and (app2 / "Contents" / "Info.plist").read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("case", ["sha", "signature"])
def test_a_bad_update_leaves_the_app_alone(tmp_path: Path, swaps, case: str) -> None:
    app = _app(tmp_path)
    archive, sha = _archive(tmp_path)
    run = FakeMac(verify_rc=1 if case == "signature" else 0)
    error = updater.install_mac_app(app, archive, sha256="0" * 64 if case == "sha" else sha, version="2.31.1", run=run)
    assert error and swaps == []
    assert (app / "Contents" / "Info.plist").read_text(encoding="utf-8") == "old"
    assert not app.with_name("UEFN Ducky.app.prev").exists() and not app.with_name(".UEFN Ducky.app.new").exists()
    assert not archive.exists()


def _status(**kw):
    return {"update_available": True, "remote_version": "2.31.1", "download_url": "https://uefnducky.org/download",
            "asset": {"url": "https://x/UEFN-Ducky-2.31.1-macos-arm64.app.zip", "sha256": "a"}, **kw}


def test_an_app_this_user_cannot_replace_gets_the_dmg(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    monkeypatch.setattr(updater.os, "access", lambda p, mode: False)
    got = updater.apply_mac_update(status=_status(), app=app)
    assert got["ok"] is False and got["action"] == "manual" and "DMG" in got["error"]
    assert updater.apply_mac_update(status=_status(asset={"url": "http://x/a.zip", "sha256": "a"}), app=app)["ok"] is False


def test_restart_downloads_swaps_and_starts_the_watchdog(tmp_path: Path, monkeypatch) -> None:
    app = _app(tmp_path)
    started, exited = [], []
    monkeypatch.setattr(updater, "_stop_all_agents", lambda: None)
    monkeypatch.setattr(updater, "_download", lambda url, dest: dest.write_bytes(b"zip") and None)
    monkeypatch.setattr(updater, "install_mac_app", lambda a, archive, **kw: None)
    monkeypatch.setattr(updater, "start_watchdog", lambda a, **kw: started.append((a, kw)))
    monkeypatch.setattr(updater, "_exit_self_after_delay", lambda: exited.append(True))
    got = updater.apply_mac_update(status=_status(), app=app)
    assert got["ok"] is True and exited == [True]
    path, kw = started[0]
    assert path == app and kw["script"] is updater._MAC_WATCHDOG_SH and kw["previous"].name == "UEFN Ducky.app.prev"


def _posix_sh() -> str | None:
    """/bin/sh, or Git for Windows' bash (System32's bash.exe is WSL: other paths)."""
    if os.name != "nt":
        return shutil.which("sh")
    git = shutil.which("git")
    roots = ([Path(git).resolve().parent.parent] if git else []) + [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git"]
    found = [r / sub / "bash.exe" for r in roots for sub in ("bin", "usr/bin") if (r / sub / "bash.exe").is_file()]
    return str(found[0]) if found else None


@pytest.mark.skipif(_posix_sh() is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("healthy", [True, False])
def test_watchdog_rolls_back_an_app_that_never_gets_ready(tmp_path: Path, healthy: bool) -> None:
    app = tmp_path / "UEFN Ducky.app"
    prev = tmp_path / "UEFN Ducky.app.prev"
    ok, failed, ran = tmp_path / "swap.ok", tmp_path / "swap.failed", tmp_path / "ran"
    for folder, label, code in ((app, "new", "touch \"$OK\"; exit 0" if healthy else "exit 1"),
                                (prev, "old", "exit 0")):
        exe = folder / "Contents" / "MacOS" / "UEFN-Ducky"
        exe.parent.mkdir(parents=True)
        exe.write_text(f"#!/bin/sh\necho {label} >> \"$RAN\"\n{code}\n", encoding="utf-8", newline="\n")
        os.chmod(exe, 0o755)
    env = {**os.environ, "OK": ok.as_posix(), "RAN": ran.as_posix()}
    args = [app, prev, ok, failed, "5", "2.31.0", "2.31.1", "1", "UEFN-Ducky"]
    subprocess.run([_posix_sh(), "-c", updater._MAC_WATCHDOG_SH, "watch", *[Path(a).as_posix() if isinstance(a, Path) else a
                                                                        for a in args]],
                   env=env, timeout=30, check=False)
    lines = ran.read_text(encoding="utf-8").split()
    if healthy:
        assert lines == ["new"] and prev.is_dir() and not failed.exists()
    else:
        # The new app died: the old one is back in place and started, and the failure is recorded.
        assert lines == ["new", "old"] and not prev.exists()
        assert (app / "Contents" / "MacOS" / "UEFN-Ducky").read_text(encoding="utf-8").count("old") == 1
        assert '"from": "2.31.1"' in failed.read_text(encoding="utf-8")
