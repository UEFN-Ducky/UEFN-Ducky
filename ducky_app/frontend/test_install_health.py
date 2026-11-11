"""The install folder's EXEs between runs: changes recorded, a broken root launcher put back."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from frontend import install_health as ih


def _pe(body: bytes = b"code", size: int = 1024) -> bytes:
    """A smallest whole PE: MZ, PE header at 0x40, one section whose bytes end at ``size``."""
    data = bytearray(size)
    data[:2] = b"MZ"
    data[60:64] = (0x40).to_bytes(4, "little")
    data[0x40:0x44] = b"PE\0\0"
    data[0x46:0x48] = (1).to_bytes(2, "little")  # one section
    data[0x54:0x56] = (0).to_bytes(2, "little")  # no optional header
    row = 0x40 + 24
    data[row:row + 8] = b".text\0\0\0"
    data[row + 16:row + 20] = (size - 0x200).to_bytes(4, "little")  # SizeOfRawData
    data[row + 20:row + 24] = (0x200).to_bytes(4, "little")  # PointerToRawData
    data[0x200:0x200 + len(body)] = body
    return bytes(data)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "Programs" / "UEFN Ducky"
    root.mkdir(parents=True)
    for name in ih.FILES:
        (root / name).write_bytes(_pe(name.encode()))
    return root


def _bump(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    later = time.time() + 5
    os.utime(path, (later, later))


def _log_rows() -> list[dict]:
    path = ih.state_dir() / ih.LOG_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_exe_ok_tells_whole_programs_from_zeros_and_cut_short_ones(tmp_path) -> None:
    good = tmp_path / "good.exe"
    good.write_bytes(_pe())
    assert ih.exe_ok(good) and ih.starts_with_mz(good)
    zeros = tmp_path / "zeros.exe"
    zeros.write_bytes(b"\0" * 1024)
    assert not ih.exe_ok(zeros) and not ih.starts_with_mz(zeros)
    cut = tmp_path / "cut.exe"
    cut.write_bytes(_pe()[:700])  # headers fine, the section's bytes are not all there
    assert ih.starts_with_mz(cut) and not ih.exe_ok(cut)
    assert not ih.exe_ok(tmp_path / "missing.exe")
    fp = ih.fingerprint(zeros)
    assert fp["zeros"] is True and fp["ok"] is False and fp["size"] == 1024


def test_unchanged_install_records_nothing(tmp_path) -> None:
    root = _root(tmp_path)
    assert ih.check_at_start(root) == {"changes": []}  # first look: nothing to compare
    assert ih.check_at_start(root) == {"changes": []}
    assert _log_rows() == []


def test_a_launcher_zeroed_while_off_is_logged_and_put_back(tmp_path, monkeypatch) -> None:
    tracked: list[tuple] = []
    monkeypatch.setattr(ih, "_track", lambda *a: tracked.append(a))
    root = _root(tmp_path)
    ih.record("exit", root)
    assert ih.good_copy().read_bytes() == (root / ih.LAUNCHER).read_bytes()
    _bump(root / ih.LAUNCHER, b"\0" * 1024)  # what the PC restart left
    report = ih.check_at_start(root)
    change = report["changes"][0]
    assert change["file"] == ih.LAUNCHER and change["after"]["zeros"] and not change["after"]["ok"]
    assert change["before"]["ok"] and report["setup_ran"] is False and report["last_seen_by"] == "exit"
    assert report["repaired"] is True
    assert ih.exe_ok(root / ih.LAUNCHER)
    assert [r["event"] for r in _log_rows()] == ["changed_while_off", "launcher_repaired"]
    assert ("install_exe_broken", ih.LAUNCHER, "crash") in tracked
    assert ("install_launcher_repaired", ih.LAUNCHER, "crash") in tracked
    assert ih.check_at_start(root) == {"changes": []}  # the repair is recorded, not reported again


def test_a_truncated_launcher_with_a_bad_good_copy_is_left_alone(tmp_path) -> None:
    root = _root(tmp_path)
    ih.check_at_start(root)
    ih.good_copy().write_bytes(b"\0" * 1024)  # a good copy that went bad is never used
    _bump(root / ih.LAUNCHER, _pe()[:700])
    report = ih.check_at_start(root)
    assert report["repaired"] is False
    assert (root / ih.LAUNCHER).read_bytes() == _pe()[:700]
    assert _log_rows()[-1]["event"] == "launcher_broken_no_good_copy"


def test_a_change_made_by_setup_is_logged_but_not_sent(tmp_path, monkeypatch) -> None:
    tracked: list[tuple] = []
    monkeypatch.setattr(ih, "_track", lambda *a: tracked.append(a))
    root = _root(tmp_path)
    ih.record("exit", root)
    _bump(root / ih.STUB, _pe(b"newer stub"))
    _bump(root / ih.UNINSTALL_DATA, b"inno")
    report = ih.check_at_start(root)
    assert [c["file"] for c in report["changes"]] == [ih.STUB]
    assert report["setup_ran"] is True and "repaired" not in report
    assert tracked == []
    assert _log_rows()[0]["setup_ran"] is True


def test_a_changed_launcher_becomes_the_good_copy_only_when_whole(tmp_path) -> None:
    root = _root(tmp_path)
    ih.record("ready", root)
    newer = _pe(b"1.2.492 launcher")
    (root / ih.LAUNCHER).write_bytes(newer)
    ih.record("refresh", root)
    assert ih.good_copy().read_bytes() == newer
    (root / ih.LAUNCHER).write_bytes(b"\0" * 1024)
    assert ih.keep_good_copy(root) is False
    assert ih.good_copy().read_bytes() == newer


def test_setup_logs_keep_the_newest_five(tmp_path) -> None:
    folder = ih.setup_logs_dir()
    folder.mkdir(parents=True)
    for i in range(7):
        log = folder / f"setup-2026110{i}-000000.log"
        log.write_text("x")
        os.utime(log, (1_000_000 + i, 1_000_000 + i))
    args = ih.setup_log_args()
    assert len(args) == 1 and args[0].startswith("/LOG=") and args[0].endswith(".log")
    left = sorted(p.name for p in folder.glob("setup-*.log"))
    assert len(left) == 4 and left[0] == "setup-20261103-000000.log"  # + this run's = 5


def test_never_touches_the_real_install_from_tests_or_dev_runs(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert os.environ.get("DUCKY_TESTS_ISOLATED_APPDATA")
    assert ih.install_root() is None
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("DUCKY_TESTS_ISOLATED_APPDATA")
    assert ih.install_root() is None
    assert ih.check_at_start() == {}
