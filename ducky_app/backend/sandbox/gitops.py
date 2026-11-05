"""Git in a sandbox: shallow clones of public repos, and clean worktrees of the person's own.

A worktree checks out one commit of one of the person's projects into the sandbox, so a
build or test sees exactly that commit and never another session's half-done files. The
sandbox remembers its worktrees and removes them (``git worktree remove``) when cleared.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.sandbox import manifest, paths
from backend.sandbox.env import sandbox_env

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class GitError(RuntimeError):
    """A git step that failed. The message is shown to the agent."""


def git_exe(env: dict[str, str] | None = None) -> str:
    found = shutil.which("git", path=(env or os.environ).get("PATH"))
    if not found:
        raise GitError("Git isn't installed. Call sandbox_setup(runtime='git') first.")
    return found


def _git(args: list[str], *, cwd: str, env: dict[str, str], timeout: float = 600.0) -> str:
    try:
        proc = subprocess.run([git_exe(env), *args], cwd=cwd, env=env, capture_output=True, text=True,
                              errors="replace", timeout=timeout, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} took longer than {int(timeout)} s") from exc
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or f"git {args[0]} failed").strip()[-1500:]
        if "' too big" in said or "Filename too long" in said:
            said += (f"\nThe folder path is too long for git on Windows ({len(cwd)} characters before the repo's "
                     "own folders). Give the clone a short name.")
        raise GitError(said)
    return (proc.stdout or "").strip()


def _folder_name(raw: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.") or fallback
    if not _NAME.fullmatch(name):
        name = fallback
    return name


def clone(conv_id: str, repo: str, ref: str = "", name: str = "") -> dict[str, Any]:
    from backend.tools.core.web_lookup import reject_url

    url = (repo or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        url = f"https://github.com/{url}.git"
    if not url.startswith("https://"):
        raise GitError("Clone public https repositories only (or owner/name for GitHub).")
    why = reject_url(url)
    if why:
        raise GitError(f"{url}: {why}")
    box = paths.sandbox_dir(conv_id, create=True)
    env = sandbox_env(box)
    target = box / "work" / _folder_name(name or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"), "repo")
    if target.exists():
        raise GitError(f"{target.name} already exists in the sandbox; pass another name.")
    sha_like = bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref or ""))
    args = ["clone", "--depth", "1", "--no-tags"]
    if ref and not sha_like:
        args += ["--branch", ref]
    _git([*args, url, str(target)], cwd=str(box / "work"), env=env)
    if sha_like:
        _git(["fetch", "--depth", "1", "origin", ref], cwd=str(target), env=env)
        _git(["checkout", "--detach", ref], cwd=str(target), env=env)
    head = _git(["rev-parse", "HEAD"], cwd=str(target), env=env)
    manifest.record(box, "clone", repo=url, ref=ref, head=head, path=str(target))
    return {"ok": True, "path": str(target), "head": head}


def _known_repo(repo_path: str) -> Path:
    """One of the person's own project folders (open, recent or configured), and a git repo."""
    from backend.bridge.client import _recent_project_roots, workspace_roots

    full = os.path.realpath(os.path.abspath(repo_path))
    roots = [*workspace_roots(), *_recent_project_roots()]
    if not any(paths.is_inside(full, r) for r in roots):
        raise GitError("A worktree comes from one of your projects (open, recent or configured in Ducky).")
    out = subprocess.run(["git", "-C", full, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL) if shutil.which("git") else None
    if out is None or out.returncode != 0:
        raise GitError(f"{full} isn't a git repository (or git isn't installed).")
    return Path(out.stdout.strip())


def _records(box: Path) -> list[dict[str, str]]:
    try:
        data = json.loads((box / "worktrees.json").read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_records(box: Path, rows: list[dict[str, str]]) -> None:
    (box / "worktrees.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


_EOL_KEYS = ("core.autocrlf", "core.eol")


def _keep_line_endings(repo: Path, env: dict[str, str]) -> None:
    """Pin the person's line-ending settings in the repo's own config, which its worktrees
    share. Sandbox terminals run git with their own profile, so a setting from the person's
    global config was lost there and edits came out CRLF (Nov 2026). Same value, so the
    person's own git behaves as before."""
    for key in _EOL_KEYS:
        try:
            value = _git(["config", "--get", key], cwd=str(repo), env=env, timeout=30)
        except GitError:
            continue  # not set anywhere: git's default everywhere
        try:
            _git(["config", "--local", "--get", key], cwd=str(repo), env=env, timeout=30)
            continue  # already in the repo's config
        except GitError:
            pass
        try:
            _git(["config", "--local", key, value], cwd=str(repo), env=env, timeout=30)
        except GitError:
            pass


def worktree(conv_id: str, repo_path: str, commit: str = "HEAD", name: str = "",
             copy: list[str] | None = None, copy_from: str = "") -> dict[str, Any]:
    """A detached worktree of ``commit``, plus ignored files listed in ``copy`` (relative to
    the repo, or to ``copy_from`` when the inputs live in another checkout of it)."""
    repo = _known_repo(repo_path)
    box = paths.sandbox_dir(conv_id, create=True)
    env = dict(os.environ)  # git of the person's repo: their own git and config
    target = box / "work" / _folder_name(name or f"{repo.name}-{commit[:12]}", "worktree")
    if target.exists():
        raise GitError(f"{target.name} already exists in the sandbox; pass another name.")
    sha = _git(["rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=str(repo), env=env)
    _git(["worktree", "add", "--detach", str(target), sha], cwd=str(repo), env=env)
    _keep_line_endings(repo, env)
    rows = _records(box)
    rows.append({"repo": str(repo), "path": str(target)})
    _save_records(box, rows)
    source = Path(copy_from).resolve() if copy_from else repo
    if copy_from:
        _known_repo(str(source))
    copied: list[str] = []
    missing: list[str] = []
    for rel in copy or []:
        rel_path = Path(str(rel).replace("\\", "/"))
        if rel_path.is_absolute() or ".." in rel_path.parts:
            missing.append(f"{rel} (must be relative, inside the repo)")
            continue
        src, dst = source / rel_path, target / rel_path
        if not src.exists():
            missing.append(str(rel))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        copied.append(str(rel))
    manifest.record(box, "worktree", repo=str(repo), commit=sha, path=str(target), copied=copied)
    return {"ok": True, "path": str(target), "commit": sha, "copied": copied, "missing": missing}


def remove_worktrees(conv_id: str) -> list[str]:
    box = paths.sandbox_dir(conv_id)
    removed: list[str] = []
    for row in _records(box):
        repo, path = row.get("repo", ""), row.get("path", "")
        if not repo or not path:
            continue
        try:
            _git(["worktree", "remove", "--force", path], cwd=repo, env=dict(os.environ), timeout=120)
            removed.append(path)
        except GitError:
            pass
        try:
            _git(["worktree", "prune"], cwd=repo, env=dict(os.environ), timeout=60)
        except GitError:
            pass
    if (box / "worktrees.json").exists():
        _save_records(box, [])
    return removed
