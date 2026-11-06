"""versions/current.json names the folder a start lands on, so Ducky-Launcher.exe can pick
it without Python: the same pick as :func:`app_versions.newest_usable`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from frontend import app_versions as av


@pytest.fixture(autouse=True)
def _frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    stub = tmp_path / "Programs" / "UEFN Ducky" / "UEFN-Ducky.exe"
    monkeypatch.setenv(av.LAUNCHER_ENV, str(stub))
    return stub


def _version(version: str, *, ready: bool = True) -> Path:
    folder = av.version_dir(version)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    if ready:
        (folder / av.READY_MARKER).write_text("{}", encoding="utf-8")
    return folder


def _current() -> dict:
    return json.loads((av.versions_dir() / av.CURRENT_FILE).read_text(encoding="utf-8"))


def _expected(installed: str, stub: Path) -> tuple[str, str]:
    target = av.newest_usable(installed)
    if target:
        return target[0], f"versions/{target[1].name}"
    return installed, str(stub.parent)


def test_written_on_mark_ready() -> None:
    _version("1.2.412")
    av.mark_ready("1.2.412")
    got = _current()
    assert (got["version"], got["dir"]) == ("1.2.412", "versions/app-1.2.412")
    assert got["written"] > 0
    assert (av.versions_dir().parent / got["dir"] / "UEFN-Ducky.exe").is_file()


def test_rewritten_on_rollback(monkeypatch, _frozen) -> None:
    _version("1.2.410")
    _version("1.2.412")
    av.write_current("1.2.412", "1.2.409")
    monkeypatch.setattr(av, "running_from_versions", lambda: True)
    monkeypatch.setattr(av, "start_detached", lambda exe, args, env=None: True)
    _frozen.parent.mkdir(parents=True)
    _frozen.write_bytes(b"MZ")
    assert av.fall_back_after_failed_start("1.2.412", "crashed")
    got = _current()
    assert (got["version"], got["dir"]) == ("1.2.410", "versions/app-1.2.410")
    # The last downloaded version turns bad too: back to the installed EXE.
    av.mark_bad("1.2.410", "crashed")
    av.repoint_to_installed_pick()
    got = _current()
    assert (got["version"], got["dir"]) == ("1.2.409", str(_frozen.parent))


@pytest.mark.parametrize(
    "installed, layout",
    [
        ("1.2.409", {}),
        ("1.2.409", {"1.2.410": "ready", "1.2.412": "ready"}),
        ("1.2.409", {"1.2.410": "ready", "1.2.412": "bad", "1.2.413": "partial"}),
        ("1.2.412", {"1.2.410": "ready"}),
        ("1.2.409", {"1.2.411": "partial"}),
    ],
)
def test_equal_to_newest_usable_across_layouts(monkeypatch, _frozen, installed, layout) -> None:
    for version, state in layout.items():
        _version(version, ready=state != "partial")
        if state == "bad":
            av.mark_bad(version, "crashed")
    started: list[Path] = []
    monkeypatch.setattr(av, "start_detached", lambda exe, args, env=None: started.append(exe) or True)
    monkeypatch.setattr(av.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr(sys, "executable", str(_frozen))
    want = _expected(installed, _frozen)
    try:
        av.hand_off(installed, bridge=False)
    except SystemExit:
        pass
    got = _current()
    assert (got["version"], got["dir"]) == want
    assert got["installed"] == installed


def test_unwritable_folder_does_not_fail_the_start(monkeypatch) -> None:
    def boom(path, data):
        raise PermissionError("read-only")

    monkeypatch.setattr(av, "_write_json", boom)
    _version("1.2.412")
    av.write_current("1.2.412", "1.2.409")
    av.mark_ready("1.2.412")
    assert not (av.versions_dir() / av.CURRENT_FILE).exists()


def test_dev_runs_never_write(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False)
    _version("1.2.412")
    av.mark_ready("1.2.412")
    assert not (av.versions_dir() / av.CURRENT_FILE).exists()


def test_additive_file_older_versions_ignore() -> None:
    """Older versions only list app-<v> folders and read boot.json / handover.json."""
    _version("1.2.412")
    av.write_current("1.2.412", "1.2.409")
    assert [v for v, _ in av.ready_versions()] == ["1.2.412"]
    assert av.read_boot() == {}
    assert av.take_handover() == {}
    assert av.newest_usable("1.2.409")[0] == "1.2.412"
