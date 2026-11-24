"""Release one compiled desktop Store plugin for Windows x64, Linux x86_64 and Linux arm64.

The "Release a desktop Store plugin" workflow runs this in the plugin's fresh worktree:

    cd <UEFN-Ducky>/ducky_app && python -m backend.uefn_plugins.release_platforms <plugin> --publish --changelog TEXT

1. Windows: the plugin's own ``scripts/build_zip.build_compiled_zip(release=True)`` on this PC.
2. Linux x86_64 and arm64: the same call with ``target=`` in a ``python:3.13-bookworm``
   container (``--platform linux/arm64`` runs under Docker's QEMU; Nuitka can't cross-compile).
3. Every zip uploads with the plugin's ``scripts/release.upload_with_ticket``, then ONE
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
import subprocess
import sys
import zipfile
from pathlib import Path

from backend.store.plugin_artifacts import artifact_for_build

DUCKY_REPO = Path(__file__).resolve().parents[3]
IMAGE = "python:3.13-bookworm"
LINUX_TARGETS = {"linux-x86_64": "linux/amd64", "linux-arm64": "linux/arm64"}
# Docker Desktop has no arm64 emulation until binfmt is registered ("exec format error");
# the same image docker/setup-qemu-action uses. It lasts until Docker restarts.
QEMU_SETUP = ["docker", "run", "--privileged", "--rm", "tonistiigi/binfmt", "--install", "arm64"]


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


def build(plugin: Path, run=None) -> list[Path]:
    """Windows, linux-x86_64, linux-arm64 zips in ``plugin/deploy``, in that order. Windows
    builds first: its build prunes every older zip, a Linux build only its own target's."""
    run = run or subprocess.run
    bz = _script(plugin, "build_zip")
    # This kit, as the containers get it: a plugin looking only next to its own checkout
    # (forge's build_zip) found no UEFN-Ducky from a sandbox worktree.
    os.environ.setdefault("DUCKY_REPO", str(DUCKY_REPO))
    win = Path(bz.build_compiled_zip(release=True))
    tagged = win.with_name(win.name.replace("-compiled.ducky-plugin.zip", "-windows-x64-compiled.ducky-plugin.zip"))
    win.replace(tagged)
    zips = [tagged]
    for target, platform in LINUX_TARGETS.items():
        if platform == "linux/arm64":
            run(QEMU_SETUP, check=True)
        print(f"building {target} in {IMAGE}")
        run(linux_build_cmd(plugin, target), check=True)
        found = sorted((plugin / "deploy").glob(f"*-{target}-compiled.ducky-plugin.zip"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise SystemExit(f"{target}: the build wrote no *-{target}-compiled.ducky-plugin.zip")
        zips.append(found[-1])
    return zips


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
    win = next((a for a in artifacts if a["os"] == "windows"), None)
    if win is None:
        raise SystemExit("no Windows build: old apps would have nothing to install")
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
