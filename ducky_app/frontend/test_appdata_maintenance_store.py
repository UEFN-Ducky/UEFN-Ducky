"""AppData maintenance: leftover coding-agent prompt files."""

from __future__ import annotations

import os
import time
from pathlib import Path

from frontend import appdata_maintenance as am


def _prompt(tmp: Path, name: str, age_s: float) -> Path:
    path = tmp / name
    path.write_text("the person's message", encoding="utf-8")
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


def test_prompt_files_older_than_a_day_are_removed(tmp_path: Path) -> None:
    tmp = tmp_path / "coding_agents" / "tmp"
    tmp.mkdir(parents=True)
    for i in range(3):
        _prompt(tmp, f"prompt-{i}.txt", 25 * 3600)
    am.prune_empty_leftover_dirs(tmp_path)
    assert not tmp.exists()  # emptied, then the empty folder went too


def test_a_fresh_file_of_a_running_turn_is_kept(tmp_path: Path) -> None:
    tmp = tmp_path / "coding_agents" / "tmp"
    tmp.mkdir(parents=True)
    old = _prompt(tmp, "old.txt", 25 * 3600)
    fresh = _prompt(tmp, "running.txt", 60)
    am.prune_empty_leftover_dirs(tmp_path)
    assert not old.exists() and fresh.exists()
