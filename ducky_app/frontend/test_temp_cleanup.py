"""The one-file EXE leaks its extraction dir whenever it is killed.

Deleting those is destructive if the judgement is wrong, so these tests pin
both halves: what gets reclaimed, and — more importantly — everything that
must be left alone.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from frontend import temp_cleanup
from frontend.temp_cleanup import (
    MIN_AGE_S,
    _dir_in_use,
    _lock_probe_target,
    sweep_stale_extract_dirs,
)


def _extract_dir(root: Path, name: str, *, dll: bool = True, age_s: float = MIN_AGE_S + 60) -> Path:
    """A directory shaped like a PyInstaller one-file extraction."""
    d = root / name
    (d / "frontend").mkdir(parents=True)
    (d / "base_library.zip").write_bytes(b"x" * 1024)
    (d / "frontend" / "app.py").write_bytes(b"y" * 512)
    if dll:
        (d / "python313.dll").write_bytes(b"z" * 2048)
    old = time.time() - age_s
    os.utime(d, (old, old))
    return d


def test_reclaims_a_dead_extraction_dir(tmp_path: Path) -> None:
    d = _extract_dir(tmp_path, "_MEI111")
    removed, freed = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 1
    assert freed >= 1024 + 512 + 2048
    assert not d.exists()


def test_never_touches_a_dir_still_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = _extract_dir(tmp_path, "_MEI_live")
    dead = _extract_dir(tmp_path, "_MEI_dead")
    monkeypatch.setattr(temp_cleanup, "_dir_in_use", lambda e: e.name == "_MEI_live")
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 1
    assert live.exists(), "a live process's bundle must survive"
    assert not dead.exists()


def test_skips_a_dir_that_may_still_be_extracting(tmp_path: Path) -> None:
    # Young dir: the DLL may not be written yet, so the lock probe cannot see
    # a live process. Age is the only guard that works during extraction.
    fresh = _extract_dir(tmp_path, "_MEI_fresh", dll=False, age_s=1)
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 0
    assert fresh.exists()


def test_never_deletes_our_own_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mine = _extract_dir(tmp_path, "_MEI_self")
    monkeypatch.setattr(temp_cleanup.sys, "_MEIPASS", str(mine), raising=False)
    monkeypatch.setattr(temp_cleanup, "_dir_in_use", lambda e: False)
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 0
    assert mine.exists(), "the running process's own bundle must survive"


def test_only_pyinstaller_dirs_are_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keep_dir = tmp_path / "important-work"
    keep_dir.mkdir()
    (keep_dir / "data.txt").write_text("keep me")
    keep_file = tmp_path / "_MEI_not_a_dir.txt"
    keep_file.write_text("keep me too")
    gone = _extract_dir(tmp_path, "_MEI222")
    monkeypatch.setattr(temp_cleanup, "_dir_in_use", lambda e: False)
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 1
    assert not gone.exists()
    assert keep_dir.exists() and (keep_dir / "data.txt").read_text() == "keep me"
    assert keep_file.exists()


def test_partially_removed_leftovers_are_finished_off(tmp_path: Path) -> None:
    # Nothing lockable left: a half-cleaned corpse, safe to remove.
    d = _extract_dir(tmp_path, "_MEI_partial", dll=False)
    for pyd in d.rglob("*.pyd"):
        pyd.unlink()
    assert _lock_probe_target(d) is None
    assert _dir_in_use(d) is False
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 1
    assert not d.exists()


def test_budget_stops_the_sweep_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        _extract_dir(tmp_path, f"_MEI_b{i}")
    monkeypatch.setattr(temp_cleanup, "_dir_in_use", lambda e: False)
    surviving = lambda: sorted(p.name for p in tmp_path.glob("_MEI*"))  # noqa: E731
    assert sweep_stale_extract_dirs(tmp_path, budget_s=0) == (0, 0), "a zero budget must do no work"
    assert len(surviving()) == 5
    # And with a real budget the same set is reclaimed, so the guard above is
    # the budget and not some other reason nothing happened.
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=10)
    assert removed == 5
    assert surviving() == []


def test_env_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _extract_dir(tmp_path, "_MEI_off")
    monkeypatch.setenv(temp_cleanup.DISABLE_ENV, "1")
    assert sweep_stale_extract_dirs(tmp_path, budget_s=5) == (0, 0)
    assert d.exists()


def test_missing_temp_dir_is_not_an_error(tmp_path: Path) -> None:
    assert sweep_stale_extract_dirs(tmp_path / "nope", budget_s=5) == (0, 0)


def test_unreadable_dir_is_treated_as_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _extract_dir(tmp_path, "_MEI_unreadable")

    def boom(self, *a, **k):  # noqa: ANN001
        raise OSError("denied")

    monkeypatch.setattr(Path, "glob", boom)
    # When we cannot inspect a directory we must not delete it.
    assert _dir_in_use(d) is True


def test_lock_probe_reads_a_free_dll_as_not_in_use(tmp_path: Path) -> None:
    d = _extract_dir(tmp_path, "_MEI_free")
    assert _lock_probe_target(d) == d / "python313.dll"
    assert _dir_in_use(d) is False


def test_lock_probe_reports_in_use_when_the_dll_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    d = _extract_dir(tmp_path, "_MEI_locked")
    real_open = builtins.open

    def fake_open(path, mode="r", *a, **k):  # noqa: ANN001
        # Exactly what Windows does while a live process has the image mapped.
        if str(path).endswith("python313.dll") and "b" in mode and "+" in mode:
            raise PermissionError("mapped by a live process")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert _dir_in_use(d) is True
    removed, _ = sweep_stale_extract_dirs(tmp_path, budget_s=5)
    assert removed == 0
    assert d.exists()


def test_extension_modules_are_probed_when_there_is_no_dll(tmp_path: Path) -> None:
    d = _extract_dir(tmp_path, "_MEI_pyd", dll=False)
    (d / "_socket.pyd").write_bytes(b"p" * 16)
    assert _lock_probe_target(d) == d / "_socket.pyd"


# ── Our own scratch trees (pytest AppData roots, e2e browser profiles) ───────


def _scratch(root: Path, name: str, *, age_s: float = 7200 + 60, mb: int = 1) -> Path:
    d = root / name
    (d / "Default").mkdir(parents=True)
    (d / "Default" / "blob").write_bytes(b"x" * (mb * 1024))
    old = time.time() - age_s
    os.utime(d, (old, old))
    return d


def test_sweeps_only_the_named_prefix(tmp_path: Path) -> None:
    mine = _scratch(tmp_path, "ducky-e2e-viewer-aaa")
    other = _scratch(tmp_path, "ducky-tests-appdata-bbb")
    unrelated = _scratch(tmp_path, "someone-elses-work")
    removed, _ = temp_cleanup.sweep_stale_temp_dirs("ducky-e2e-viewer-", temp_dir=tmp_path, budget_s=5)
    assert removed == 1
    assert not mine.exists()
    assert other.exists() and unrelated.exists(), "other prefixes must be untouched"


def test_recent_scratch_is_left_for_a_run_that_may_still_be_using_it(tmp_path: Path) -> None:
    fresh = _scratch(tmp_path, "ducky-e2e-viewer-live", age_s=10)
    removed, _ = temp_cleanup.sweep_stale_temp_dirs("ducky-e2e-viewer-", temp_dir=tmp_path, budget_s=5)
    assert removed == 0
    assert fresh.exists()


def test_scratch_sweep_reports_bytes_and_honours_the_kill_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scratch(tmp_path, "ducky-e2e-viewer-ccc", mb=4)
    monkeypatch.setenv(temp_cleanup.DISABLE_ENV, "1")
    assert temp_cleanup.sweep_stale_temp_dirs("ducky-e2e-viewer-", temp_dir=tmp_path, budget_s=5) == (0, 0)
    monkeypatch.delenv(temp_cleanup.DISABLE_ENV)
    removed, freed = temp_cleanup.sweep_stale_temp_dirs("ducky-e2e-viewer-", temp_dir=tmp_path, budget_s=5)
    assert removed == 1 and freed >= 4 * 1024


def test_scratch_sweep_needs_a_prefix(tmp_path: Path) -> None:
    keep = _scratch(tmp_path, "ducky-e2e-viewer-ddd")
    assert temp_cleanup.sweep_stale_temp_dirs("", temp_dir=tmp_path, budget_s=5) == (0, 0)
    assert keep.exists(), "an empty prefix must never mean 'everything'"


def test_remove_tree_with_retry_gets_there_eventually(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _scratch(tmp_path, "ducky-e2e-viewer-retry", age_s=1)
    calls = {"n": 0}
    real_rmtree = temp_cleanup.shutil.rmtree

    def flaky(path, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 3:
            return  # the browser still holds a handle
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(temp_cleanup.shutil, "rmtree", flaky)
    monkeypatch.setattr(temp_cleanup.time, "sleep", lambda _s: None)
    assert temp_cleanup.remove_tree_with_retry(d, attempts=5) is True
    assert calls["n"] == 3
    assert not d.exists()


def test_remove_tree_with_retry_gives_up_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _scratch(tmp_path, "ducky-e2e-viewer-stuck", age_s=1)
    monkeypatch.setattr(temp_cleanup.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(temp_cleanup.time, "sleep", lambda _s: None)
    assert temp_cleanup.remove_tree_with_retry(d, attempts=3) is False
    assert d.exists()


def test_remove_tree_with_retry_on_a_missing_path(tmp_path: Path) -> None:
    assert temp_cleanup.remove_tree_with_retry(tmp_path / "never-existed") is True
