"""An agent's commit never sweeps in another live chat's files."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from frontend.ui_web.terminal import commit_guard

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "backend").mkdir()
    for name in ("team.py", "images.py"):
        (tmp_path / "backend" / name).write_text("v1\n")
    git("add", ".")
    git("commit", "-qm", "init")
    (tmp_path / "backend" / "team.py").write_text("mine\n")  # this chat's edit
    (tmp_path / "backend" / "images.py").write_text("theirs\n")  # the other chat's edit
    (tmp_path / "backend" / "model3d.py").write_text("theirs\n")  # the other chat's new file
    return git


WRITERS = {"team.py": ("me", "Account"), "images.py": ("other", "Meshy"), "model3d.py": ("other", "Meshy")}


def _check(command, cwd, live=lambda cid: True):
    return commit_guard.check(command, str(cwd), "me", writer=lambda p: WRITERS.get(os.path.basename(p), ("", "")),
                              live=live)


def test_a_sweeping_commit_naming_another_chats_files_is_refused(tmp_path):
    # Nov 3 2026: chat 236c4da0's "Team tab" commit took another chat's model3d.py and images.py.
    _repo(tmp_path)
    for cmd in ("git add -A && git commit -m 'Team tab'", "git add backend && git commit -m x",
                "git commit -am x", f"cd {tmp_path.as_posix()} && git add . && git commit -m x"):
        why = _check(cmd, tmp_path)
        assert "images.py (Meshy, chat other)" in why and "backend/team.py" not in why, cmd
    assert "model3d.py" in _check("git add -A && git commit -m x", tmp_path)
    assert "model3d.py" not in _check("git commit -am x", tmp_path)  # -a takes no new files


def test_own_files_by_name_an_archived_writer_and_the_override_pass(tmp_path):
    git = _repo(tmp_path)
    assert _check("git add backend/team.py && git commit -m x", tmp_path) == ""
    assert _check("git add -A && git commit -m x", tmp_path, live=lambda cid: False) == ""
    assert _check(f"git add -A && git commit -m x  # {commit_guard.OVERRIDE}", tmp_path) == ""
    assert _check("git add -A", tmp_path) == ""  # no commit in it
    git("add", "backend/images.py")  # staged earlier, by anyone: still theirs
    assert "images.py" in _check("git commit -m x", tmp_path)


SPRAWL = ["ducky_app/backend/skills/store.py", "ducky_app/backend/uefn_plugins/host.py",
          "ducky_app/frontend/ui_web/agent_turns/routing.py", "ducky_app/frontend/chat_store.py"]


def test_a_generic_message_is_warned():
    # 5f47f6cb / 340be847 / b56660c0: "General fixes and improvements" bundled many fixes.
    for cmd in ('git commit -m "General fixes and improvements"', "git commit -am 'misc fixes'",
                'git add a.py && git commit -m "Various changes"', "git commit --message=Improvements"):
        assert "name the one change this commit makes" in commit_guard.warn(cmd, ".", "me", files=["a.py"]), cmd


def test_a_commit_across_more_than_three_packages_is_warned():
    why = commit_guard.warn('git commit -m "Move plugin ownership into skills/ownership.py"', ".", "me",
                            files=SPRAWL)
    assert "spans 4 packages" in why and "backend/skills" in why and "frontend/chat_store" in why
    assert "name the one change" not in why
    assert commit_guard.warn("git commit -m x", ".", "me", files=SPRAWL[:3]) == ""  # 3 is fine


def test_a_clean_commit_and_non_commits_get_no_warning(tmp_path):
    clean = 'git add ducky_app/backend/skills/ownership.py && git commit -m "Move plugin ownership into skills/ownership.py"'
    assert commit_guard.warn(clean, ".", "me", files=["ducky_app/backend/skills/ownership.py",
                                                    "ducky_app/backend/skills/test_ownership.py"]) == ""
    assert commit_guard.warn('git log --grep "general fixes"', ".", "me") == ""
    assert commit_guard.warn('git commit -m "General fixes"', ".", "") == ""  # the person, not an agent
    _repo(tmp_path)  # real lookup of what is staged: two files in one package
    assert commit_guard.warn("git add backend && git commit -m 'Team tab: roles'", str(tmp_path), "me") == ""
