from __future__ import annotations

import json
from pathlib import Path

from backend.memory import scope


def _plan(repo: str) -> dict:
    return {"title": "t", "body_markdown": f"**Repo:** `{repo}`", "nodes": []}


def test_plan_repo_line_wins(monkeypatch, tmp_path):
    repo = tmp_path / "Release"
    repo.mkdir()
    monkeypatch.setattr("backend.workspace.lanes.project_roots", lambda: [str(repo), str(tmp_path / "Limo")])
    got = scope.memory_root("chat-1", str(tmp_path / "Limo"), plan=_plan(str(repo)))
    assert got.replace("\\", "/").lower() == str(repo).replace("\\", "/").lower()


def test_sandbox_worktree_maps_to_its_repo(monkeypatch, tmp_path):
    box = tmp_path / "box"
    wt = box / "work" / "seat"
    wt.mkdir(parents=True)
    repo = tmp_path / "Release"
    repo.mkdir()
    (box / "worktrees.json").write_text(json.dumps([{"repo": str(repo), "path": str(wt)}]), encoding="utf-8")
    monkeypatch.setattr("backend.sandbox.paths.sandbox_dir", lambda conv_id, create=False: box)
    monkeypatch.setattr(scope, "_common_repo", lambda cwd: "")
    got = scope.memory_root("chat-1", str(tmp_path / "Limo"), plan={"title": "no repo", "nodes": []})
    assert Path(got) == repo


def test_cwd_inside_worktree_uses_git_common_dir(monkeypatch, tmp_path):
    box = tmp_path / "box"
    wt = box / "work" / "seat"
    wt.mkdir(parents=True)
    monkeypatch.setattr("backend.sandbox.paths.sandbox_dir", lambda conv_id, create=False: box)
    monkeypatch.setattr(scope, "_common_repo", lambda cwd: "C:/repos/Release")
    got = scope.memory_root("chat-1", "C:/Limo", plan={"nodes": []}, cwd=str(wt))
    assert got == "C:/repos/Release"


def test_otherwise_open_project(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.sandbox.paths.sandbox_dir", lambda conv_id, create=False: tmp_path / "none")
    assert scope.memory_root("chat-1", "C:/Limo", plan={"nodes": []}) == "C:/Limo"


def test_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(scope, "_chat_plan", boom)
    assert scope.memory_root("chat-1", "C:/Limo") == "C:/Limo"
