#!/usr/bin/env python3
"""Publish the built panel bundle to the panel host (GitHub Pages).

The phone panel for direct (tunnel-free) Remote View is served from
``https://panel.uefnducky.org`` — the ``gh-pages`` branch of this repo,
fronted by GitHub's CDN. Layout of that branch:

    /sw.js                 Service Worker (root scope; forwards desktop assets)
    /CNAME                 panel.uefnducky.org
    /<version>/…           one immutable copy of web/dist per app version
    /latest/…              copy of the newest version
    /index.html            tiny redirect to /latest/

Every desktop version keeps its own folder so an older desktop keeps
matching the panel it was built with. ``latest`` is what the site loads
first; the site swaps to ``/<desktop version>/`` once it learns the
version from the desktop's answer.

Usage (from repo root):
  py release/publish_panel.py               # build dist, publish current version
  py release/publish_panel.py --no-build    # publish the dist already on disk
  py release/publish_panel.py --dry-run     # stage into a temp worktree, don't push
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "ducky_app" / "frontend" / "ui_web" / "web"
DIST = WEB / "dist"
INIT = ROOT / "ducky_app" / "frontend" / "__init__.py"
BRANCH = "gh-pages"
CNAME = "panel.uefnducky.org"


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("version not found in frontend/__init__.py")
    return m.group(1)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd or ROOT), check=True)


def build() -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "run", "build"], cwd=WEB)
    if not (DIST / "index.html").is_file():
        raise SystemExit("web/dist/index.html missing after build")


def redirect_html() -> str:
    return (
        "<!doctype html><meta charset=utf-8>"
        "<meta http-equiv=refresh content=\"0; url=./latest/\">"
        "<title>UEFN Ducky panel</title><a href=\"./latest/\">UEFN Ducky panel</a>\n"
    )


def stage(work: Path, ver: str) -> None:
    target = work / ver
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DIST, target)
    latest = work / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(DIST, latest)
    sw = DIST / "sw.js"
    if not sw.is_file():
        raise SystemExit("dist/sw.js missing (public/sw.js should be copied by Vite)")
    shutil.copy2(sw, work / "sw.js")
    (work / "CNAME").write_text(CNAME + "\n", encoding="utf-8")
    (work / ".nojekyll").write_text("", encoding="utf-8")
    (work / "index.html").write_text(redirect_html(), encoding="utf-8")
    (work / "versions.json").write_text(
        __import__("json").dumps(sorted(p.name for p in work.iterdir() if p.is_dir() and re.match(r"^\d+\.\d+\.\d+$", p.name))),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote", default="origin")
    args = ap.parse_args()
    ver = version()
    if not args.no_build:
        build()
    work = Path(tempfile.mkdtemp(prefix="ducky-panel-"))
    try:
        # Fresh worktree on gh-pages (create the branch if the remote lacks it).
        subprocess.run(["git", "fetch", args.remote, BRANCH], cwd=str(ROOT), check=False)
        has_remote = subprocess.run(
            ["git", "rev-parse", "--verify", f"{args.remote}/{BRANCH}"], cwd=str(ROOT), capture_output=True
        ).returncode == 0
        shutil.rmtree(work)
        if has_remote:
            run(["git", "worktree", "add", "--detach", str(work), f"{args.remote}/{BRANCH}"])
        else:
            run(["git", "worktree", "add", "--detach", str(work)])
            run(["git", "checkout", "--orphan", BRANCH], cwd=work)
            run(["git", "rm", "-rfq", "."], cwd=work)
        stage(work, ver)
        run(["git", "add", "-A"], cwd=work)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(work), capture_output=True, text=True).stdout
        if not status.strip():
            print("panel: nothing changed")
            return 0
        run(["git", "-c", "user.name=ducky-release", "-c", "user.email=release@uefnducky.org", "commit", "-qm", f"panel {ver}"], cwd=work)
        if args.dry_run:
            print(f"panel: staged {ver} in {work} (dry run, not pushed)")
            return 0
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(work), capture_output=True, text=True).stdout.strip()
        run(["git", "push", args.remote, f"{head}:refs/heads/{BRANCH}"], cwd=work)
        print(f"panel: published {ver} → https://{CNAME}/{ver}/ and /latest/")
        return 0
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(work)], cwd=str(ROOT), check=False)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
