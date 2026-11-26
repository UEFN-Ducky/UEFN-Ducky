"""Release one compiled desktop Store plugin for Windows x64, Windows arm64, Linux x86_64,
Linux arm64, macOS arm64 and macOS x64: six zips.

The "Release a desktop Store plugin" workflow runs this in the plugin's fresh worktree:

    cd <UEFN-Ducky>/ducky_app && python -m backend.uefn_plugins.release_platforms <plugin> --publish --changelog TEXT

1. Windows: the plugin's own ``scripts/build_zip.build_compiled_zip(release=True)`` on this PC.
2. Linux x86_64: the same call with ``target=`` in a ``python:3.13-bookworm`` container.
3. Linux arm64: plugin-linux-arm64.yml on GitHub's arm64 runner (same image, native; under
   Docker's QEMU a big plugin ran past 4 hours). Only a draft with no GitHub remote falls
   back to the QEMU container.
4. Windows arm64: the plugin-windows-arm64.yml workflow of UEFN-Ducky on GitHub's arm64
   runner, at the plugin's pushed commit (Windows can't emulate an arm64 build here).
5. macOS arm64 + x64: ONE plugin-macos.yml run (macos-26 and macos-15-intel), both zips
   downloaded from it (only GitHub has Macs; Nuitka can't cross-compile).
6. Every zip uploads with the plugin's ``scripts/release.upload_with_ticket``, then ONE
   ``uds_release`` names the Windows upload (what old apps install) and lists every build in
   ``artifacts`` (store/plugin_artifacts.py's shape plus its ``uploadId``).

Any failed build raises before anything uploads, and any failed upload before ``uds_release``,
so a release never half-ships.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import re
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from backend.store.plugin_artifacts import artifact_for_build

DUCKY_REPO = Path(__file__).resolve().parents[3]
IMAGE = "python:3.13-bookworm"
LINUX_TARGETS = {"linux-x86_64": "linux/amd64", "linux-arm64": "linux/arm64"}
# Docker Desktop has no arm64 emulation until binfmt is registered ("exec format error");
# the same image docker/setup-qemu-action uses. It lasts until Docker restarts.
QEMU_SETUP = ["docker", "run", "--privileged", "--rm", "tonistiigi/binfmt", "--install", "arm64"]
ARM64_WORKFLOW = "plugin-windows-arm64.yml"
ARM64_ARTIFACT = "plugin-windows-arm64"
LINUX_ARM64_WORKFLOW = "plugin-linux-arm64.yml"
LINUX_ARM64_ARTIFACT = "plugin-linux-arm64"
MACOS_WORKFLOW = "plugin-macos.yml"
MACOS_ARTIFACTS = {"plugin-macos-arm64": "macos-arm64", "plugin-macos-x64": "macos-x64"}
# Where that workflow lives (public: its arm64 runner is free).
DUCKY_GH = os.environ.get("DUCKY_GH_REPO") or "UEFN-Ducky/UEFN-Ducky"


def github_slug(url: str) -> str | None:
    """``owner/name`` of a github.com remote URL (https or ssh), else None."""
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else None


def _out(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def windows_arm64_build(plugin: Path, hold: Path) -> Path | None:
    return github_build(plugin, hold, ARM64_WORKFLOW, ARM64_ARTIFACT, "plugin arm64", "windows-arm64")


def linux_arm64_build(plugin: Path, hold: Path) -> Path | None:
    return github_build(plugin, hold, LINUX_ARM64_WORKFLOW, LINUX_ARM64_ARTIFACT,
                        "plugin linux arm64", "linux-arm64")


def macos_builds(plugin: Path, hold: Path) -> list[Path]:
    """Both Mac zips from one plugin-macos.yml run; [] for a plugin with no GitHub remote."""
    run_id = github_run(plugin, MACOS_WORKFLOW, "plugin macos", "macos")
    if run_id is None:
        return []
    return [github_download(run_id, artifact, target, hold) for artifact, target in MACOS_ARTIFACTS.items()]


def github_build(plugin: Path, hold: Path, workflow: str, artifact: str, title: str,
                 target: str) -> Path | None:
    """Dispatch ``workflow`` for the plugin's HEAD (it must be pushed), wait for it, and
    download its ``*-<target>-compiled`` zip into ``hold``. None for a plugin with no GitHub
    remote (a draft)."""
    run_id = github_run(plugin, workflow, title, target)
    return None if run_id is None else github_download(run_id, artifact, target, hold)


def github_run(plugin: Path, workflow: str, title: str, target: str) -> str | None:
    """Dispatch ``workflow`` for the plugin's pushed HEAD and wait for it to pass: its run id,
    None for a plugin with no GitHub remote."""
    try:
        slug = github_slug(_out(["git", "remote", "get-url", "origin"], plugin))
        sha = _out(["git", "rev-parse", "HEAD"], plugin)
    except (subprocess.CalledProcessError, FileNotFoundError):
        slug = None
    if not slug:
        print(f"WARNING: {plugin.name} has no GitHub remote: no {target} build on GitHub")
        return None
    request = f"{plugin.name}-{sha[:12]}-{uuid.uuid4().hex[:8]}"
    print(f"building {target} on GitHub ({DUCKY_GH} {workflow}, {slug}@{sha[:12]})")
    _out(["gh", "workflow", "run", workflow, "-R", DUCKY_GH,
          "-f", f"repo={slug}", "-f", f"sha={sha}", "-f", f"request={request}"])
    run_id = ""
    for _ in range(60):  # a dispatched run takes a few seconds to be listed
        time.sleep(5)
        runs = json.loads(_out(["gh", "run", "list", "-R", DUCKY_GH, "-w", workflow, "-L", "30",
                                "--json", "databaseId,displayTitle"]) or "[]")
        run_id = next((str(r["databaseId"]) for r in runs if r.get("displayTitle") == f"{title} {request}"), "")
        if run_id:
            break
    if not run_id:
        raise SystemExit(f"{target}: the {workflow} run for {request} never showed up")
    if subprocess.run(["gh", "run", "watch", run_id, "-R", DUCKY_GH, "--exit-status", "--interval", "30"]).returncode:
        raise SystemExit(f"{target} build failed: https://github.com/{DUCKY_GH}/actions/runs/{run_id} "
                         "(is the commit pushed? a private repo needs the PLUGIN_REPO_TOKEN secret)")
    return run_id


def github_download(run_id: str, artifact: str, target: str, hold: Path) -> Path:
    """The one ``*-<target>-compiled`` zip of ``artifact`` from run ``run_id``, moved into ``hold``."""
    got = Path(tempfile.mkdtemp(prefix=f"ducky-{target}-"))
    _out(["gh", "run", "download", run_id, "-R", DUCKY_GH, "-n", artifact, "-D", str(got)])
    found = sorted(got.glob(f"*-{target}-compiled.ducky-plugin.zip"))
    if len(found) != 1:
        raise SystemExit(f"{target}: expected one zip from run {run_id}, got {[p.name for p in found]}")
    return Path(shutil.move(str(found[0]), str(hold / found[0].name)))


def _script(plugin: Path, name: str):
    """The plugin's own ``scripts/<name>.py`` (its build and Store code, never a copy)."""
    path = plugin / "scripts" / f"{name}.py"
    if not path.is_file():
        raise SystemExit(f"{plugin.name}: no scripts/{name}.py")
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(f"plugin_{name}_{abs(hash(str(plugin)))}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _zip_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("plugin.json").decode("utf-8"))


def build_pins(repo: Path = DUCKY_REPO) -> list[str]:
    """What the bare build image lacks that the build engine imports: its CPU check's
    disassembler (iced-x86), at the version Ducky's requirements.txt pins."""
    lines = (repo / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = [line.split("#")[0].strip() for line in lines if line.strip().lower().startswith("iced-x86")]
    if not pins:
        raise SystemExit(f"{repo / 'requirements.txt'} pins no iced-x86 (the build engine's CPU check needs it)")
    return pins


def linux_build_cmd(plugin: Path, target: str) -> list[str]:
    platform = LINUX_TARGETS[target]
    # Installed here, not in each plugin's build_zip: only google's had it, so every other
    # plugin's Linux build died on 'No module named iced_x86'.
    install = ("import subprocess; subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', "
               f"'--disable-pip-version-check', '--root-user-action=ignore', *{build_pins()!r}], check=True)")
    call = (f"import sys; {install}; sys.path.insert(0, 'scripts'); import build_zip as b; "
            f"b.build_compiled_zip(release=True, target={target!r})")
    return ["docker", "run", "--rm", "--platform", platform,
            "-v", f"{plugin}:/plugin", "-v", f"{DUCKY_REPO}:/ducky:ro",
            # the pinned build kit (python-build-standalone, Nuitka) is cached per arch
            "-v", f"ducky-plugin-kit-{target}:/kit",
            "-e", "DUCKY_REPO=/ducky", "-e", "DUCKY_BUILD_KIT_DIR=/kit",
            "-e", f"DUCKYOS_BASE_URL={os.environ.get('DUCKYOS_BASE_URL', '')}",
            "-w", "/plugin", IMAGE, "python", "-c", call]


def build(plugin: Path, run=None, arm64=None, linux_arm64=None, macos=None) -> list[Path]:
    """Windows x64, linux-x86_64, linux-arm64, Windows arm64, then macOS arm64 + x64 zips, each
    moved out of ``plugin/deploy`` into a temp folder as soon as it is built."""
    run = run or subprocess.run
    arm64 = arm64 or windows_arm64_build
    linux_arm64 = linux_arm64 or linux_arm64_build
    macos = macos or macos_builds
    bz = _script(plugin, "build_zip")
    # This kit, as the containers get it: a plugin looking only next to its own checkout
    # (forge's build_zip) found no UEFN-Ducky from a sandbox worktree.
    os.environ.setdefault("DUCKY_REPO", str(DUCKY_REPO))
    # Each finished zip waits outside deploy/: a plugin's own build may prune every zip there
    # (forge's Linux build deleted the Windows one before the upload).
    hold = Path(tempfile.mkdtemp(prefix=f"ducky-release-{plugin.name}-"))
    # The GitHub builds run while this PC builds its own: one after another, a big plugin
    # (road-network) took over an hour for four builds.
    gh_linux = _background(linux_arm64, plugin, hold)
    gh_win = _background(arm64, plugin, hold)
    gh_mac = _background(macos, plugin, hold)
    win = Path(bz.build_compiled_zip(release=True))
    tagged = hold / win.name.replace("-compiled.ducky-plugin.zip", "-windows-x64-compiled.ducky-plugin.zip")
    shutil.move(str(win), str(tagged))
    zips = [tagged]
    for target, platform in LINUX_TARGETS.items():
        if platform == "linux/arm64":
            native = gh_linux()
            if native is not None:
                zips.append(native)
                continue
            run(QEMU_SETUP, check=True)
        print(f"building {target} in {IMAGE}")
        run(linux_build_cmd(plugin, target), check=True)
        found = sorted((plugin / "deploy").glob(f"*-{target}-compiled.ducky-plugin.zip"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise SystemExit(f"{target}: the build wrote no *-{target}-compiled.ducky-plugin.zip")
        zips.append(Path(shutil.move(str(found[-1]), str(hold / found[-1].name))))
    arm = gh_win()
    if arm is not None:
        zips.append(arm)
    zips += gh_mac()
    return zips


def _background(fn, *args):
    """Start ``fn(*args)`` on a daemon thread (a failed local build exits without waiting on
    GitHub); the returned call waits and gives its result or raises its error."""
    box: dict = {}

    def work():
        try:
            box["result"] = fn(*args)
        except BaseException as exc:  # SystemExit too: a failed GitHub build
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()

    def wait():
        t.join()
        if "error" in box:
            raise box["error"]
        return box["result"]

    return wait


def publish(plugin: Path, zips: list[Path], *, category: str, changelog: str) -> dict:
    rel = _script(plugin, "release")
    rel._load_dotenv()
    base = os.environ.get("DUCKYOS_BASE_URL") or os.environ.get("DUCKYOS_MARKETPLACE_URL") or "https://uefnducky.org"
    key = os.environ.get("DUCKYOS_API_KEY") or os.environ.get("DUCKYOS_MARKETPLACE_API_KEY") or ""
    if not key:
        raise SystemExit("Set DUCKYOS_API_KEY (staff key with mcp_remote scopes) to publish")
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    pid = str(manifest.get("id") or "").strip()
    artifacts = []
    for path in zips:
        data = path.read_bytes()
        art = artifact_for_build(pid, _zip_manifest(path), hashlib.sha256(data).hexdigest(), len(data))
        print(f"uploading {path.name} ({art['os']}-{art['arch']}, {len(data)} bytes)")
        artifacts.append({**art, "uploadId": rel.upload_with_ticket(base, key, path)})
    if len({(a["os"], a["arch"]) for a in artifacts}) != len(artifacts):
        raise SystemExit(f"two builds for one platform: {[(a['os'], a['arch']) for a in artifacts]}")
    win = next((a for a in artifacts if a["os"] == "windows" and a["arch"] == "x86_64"), None)
    if win is None:
        raise SystemExit("no Windows x64 build: old apps would have nothing to install")
    rel.mcp_call(base, key, "uds_ensure_category", {"name": category, "slug": category})
    args: dict = {"uploadId": win["uploadId"], "category": category, "changelog": changelog,
                  "publish": True, "artifacts": artifacts}
    for field, src in (("name", "label"), ("description", "description")):
        if str(manifest.get(src) or "").strip():
            args[field] = str(manifest[src]).strip()
    result = rel.mcp_call(base, key, "uds_release", args)
    print(json.dumps(result, indent=2))
    if result.get("ok") is False or result.get("error"):
        raise SystemExit(result.get("error") or "release failed")
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plugin", type=Path, help="the plugin repo (its plugin.json and scripts/)")
    ap.add_argument("--publish", action="store_true", help="upload all builds and publish them")
    ap.add_argument("--changelog", default="")
    ap.add_argument("--category", default=os.environ.get("UDS_CATEGORY") or "plugins")
    args = ap.parse_args(argv)
    plugin = args.plugin.resolve()
    if args.publish:
        _script(plugin, "commit_before_store").commit_and_push_before_publish(plugin, args.changelog)
    zips = build(plugin)
    for z in zips:
        print(f"built {z.name}")
    if args.publish:
        publish(plugin, zips, category=args.category, changelog=args.changelog)


if __name__ == "__main__":
    main()
