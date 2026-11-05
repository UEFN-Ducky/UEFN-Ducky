"""Commit + push the clone before any UEFN Ducky Store upload.

Store deploy refuses to run on a dirty tree. Built zips/exes and secrets stay out.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "deploy",
        "dist",
    }
)
_SKIP_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".ducky-plugin.zip",
    ".dat",
    ".env",
    ".pem",
    ".key",
)
_SKIP_NAMES = frozenset({".env", "credentials.dat", ".DS_Store"})


def skip_store_commit_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    name = p.rsplit("/", 1)[-1]
    if name in _SKIP_NAMES:
        return True
    if p.endswith(_SKIP_SUFFIXES):
        return True
    return any(part in _SKIP_DIR_NAMES for part in p.split("/"))


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        text=True,
        capture_output=True,
    )


def _porcelain(root: Path) -> list[tuple[str, str]]:
    out = _git(root, "status", "--porcelain", "-uall").stdout
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((line[:2], path.strip().strip('"')))
    return rows


def _commit_message(root: Path, changelog: str) -> str:
    msg = (changelog or "").strip()
    if msg:
        return msg
    plugin = root / "plugin.json"
    if plugin.is_file():
        try:
            data = json.loads(plugin.read_text(encoding="utf-8"))
            ver = str(data.get("version") or "").strip()
            label = str(data.get("label") or data.get("id") or root.name).strip()
            if ver:
                return f"Release {label} {ver}."
        except json.JSONDecodeError:
            pass
    return "Commit working tree before UEFN Ducky Store publish."


def commit_and_push_before_publish(root: Path, changelog: str = "") -> None:
    """Add/commit/push every shippable file. Abort if anything real stays dirty."""
    root = root.resolve()
    inside = _git(root, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise SystemExit(f"Store publish requires a git clone: {root}")

    _git(root, "add", "-A")
    for _xy, path in _porcelain(root):
        if skip_store_commit_path(path):
            _git(root, "reset", "-q", "--", path, check=False)

    staged = _git(root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode != 0:
        msg = _commit_message(root, changelog)
        commit = _git(root, "commit", "-m", msg, check=False)
        if commit.returncode != 0:
            raise SystemExit(commit.stderr or commit.stdout or "git commit failed")
        print(f"committed before Store publish: {msg}")

    leftover = [path for _xy, path in _porcelain(root) if not skip_store_commit_path(path)]
    if leftover:
        raise SystemExit(
            "Store publish refuses a dirty clone. Commit these first:\n  "
            + "\n  ".join(leftover)
        )

    remote = _git(root, "remote", check=False).stdout.split()
    if "origin" in remote:
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch == "HEAD":
            # Detached release worktree: publish onto origin's default branch.
            default = _git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", check=False).stdout.strip()
            branch = default.rsplit("/", 1)[-1] or "main"
            push = _push_detached(root, branch)
        else:
            push = _git(root, "push", "-u", "origin", "HEAD", check=False)
        if push.returncode != 0:
            raise SystemExit(push.stderr or push.stdout or "git push failed")
        print("pushed HEAD to origin before Store publish")
    else:
        print("no origin remote — Store zip is from this committed HEAD only")


PUSH_TRIES = 3


def _push_detached(root: Path, branch: str) -> subprocess.CompletedProcess[str]:
    """Push the release commit onto origin's branch. Nov 2026: a fix pushed to main while
    the release built made the push non-fast-forward, and the whole release stopped after
    a passed test run and a finished build. A rejected push now rebases the release commit
    onto the new tip and tries again; a rebase conflict still stops the release."""
    push = _git(root, "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
    for _ in range(PUSH_TRIES - 1):
        if push.returncode == 0:
            return push
        _git(root, "fetch", "-q", "origin", branch, check=False)
        incoming = _git(root, "log", "--oneline", f"HEAD..origin/{branch}", check=False).stdout.strip()
        if not incoming:
            return push  # not behind: refused for another reason (auth, hook)
        rebase = _git(root, "rebase", "-q", f"origin/{branch}", check=False)
        if rebase.returncode != 0:
            _git(root, "rebase", "--abort", check=False)
            raise SystemExit(f"{branch} moved during the release and the release commit does not rebase onto it:\n"
                             + (rebase.stderr or rebase.stdout))
        print(f"  {branch} moved during the release; rebased onto it (these are on {branch} but not in this "
              f"build):\n    " + incoming.replace("\n", "\n    "))
        push = _git(root, "push", "origin", f"HEAD:refs/heads/{branch}", check=False)
    return push


def _self_check() -> None:
    assert skip_store_commit_path("deploy/openai-1.0.34.ducky-plugin.zip")
    assert skip_store_commit_path("backend/__pycache__/x.pyc")
    assert skip_store_commit_path(".env")
    assert skip_store_commit_path("dist/UEFN-Ducky-Setup.exe")
    assert not skip_store_commit_path("plugin.json")
    assert not skip_store_commit_path("scripts/release.py")
    assert not skip_store_commit_path("backend/__init__.py")
    print("commit_before_store self-check ok")


if __name__ == "__main__":
    _self_check()
