"""Install staging leftovers older than a day are swept; a running install's folder stays."""

from __future__ import annotations

import os
import shutil
import time

from backend.uefn_plugins import staging_sweep


def _folder(root, name: str, age_hours: float):
    path = root / name
    path.mkdir(parents=True)
    (path / "plugin.json").write_text("{}", encoding="utf-8")
    then = time.time() - age_hours * 3600
    os.utime(path, (then, then))
    return path


def test_a_day_old_staging_folder_is_removed(tmp_path) -> None:
    old = _folder(tmp_path, "plugin-demo-abc", 30)
    assert staging_sweep.sweep(tmp_path) == 1
    assert not old.exists()


def test_a_folder_of_a_running_install_is_kept(tmp_path) -> None:
    young = _folder(tmp_path, "plugin-demo-new", 1)
    assert staging_sweep.sweep(tmp_path) == 0
    assert young.exists()


def test_only_prefixed_entries_are_swept(tmp_path) -> None:
    downloads = _folder(tmp_path, ".downloads", 48)
    stale = _folder(tmp_path, ".staging-pack", 48)
    assert staging_sweep.sweep(tmp_path, prefix=(".staging-", ".old-")) == 1
    assert downloads.exists() and not stale.exists()


def test_a_locked_folder_does_not_fail_the_install(tmp_path, monkeypatch) -> None:
    locked = _folder(tmp_path, "plugin-demo-locked", 30)

    def _locked(*_a, **_k):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(shutil, "rmtree", _locked)
    assert staging_sweep.sweep(tmp_path) == 0
    assert locked.exists()
    assert staging_sweep.sweep(tmp_path / "missing") == 0
