"""A sandbox worktree keeps the person's line-ending settings, even where git runs without
their global config (sandbox terminals have their own profile)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.sandbox import gitops

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def _git(*args: str, cwd: Path, env: dict[str, str]) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True).stdout.strip()


def test_a_worktree_keeps_core_autocrlf(tmp_path, monkeypatch) -> None:
    person = tmp_path / "person.gitconfig"
    person.write_text("[core]\n\tautocrlf = true\n[user]\n\tname = t\n\temail = t@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(person))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env = dict(os.environ)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo, env=env)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git("add", "a.txt", cwd=repo, env=env)
    _git("commit", "-q", "-m", "a", cwd=repo, env=env)
    box = tmp_path / "box"
    (box / "work").mkdir(parents=True)
    monkeypatch.setattr(gitops, "_known_repo", lambda path: Path(path))
    monkeypatch.setattr(gitops.paths, "sandbox_dir", lambda conv_id, create=False: box)

    out = gitops.worktree("chat", str(repo), name="wt")
    assert out["ok"]
    # The sandbox's git: no global config of the person's.
    sandbox = {**env, "GIT_CONFIG_GLOBAL": str(tmp_path / "empty.gitconfig")}
    assert _git("config", "core.autocrlf", cwd=Path(out["path"]), env=sandbox) == "true"
    assert _git("config", "--get", "core.eol", cwd=Path(out["path"]), env=sandbox) == ""  # unset stays unset
