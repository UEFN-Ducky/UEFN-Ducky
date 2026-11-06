"""Where a desktop plugin lives as a git repo (ducky_plugin_find_repo, release workflows).

Moved out of panel_automations.py, which had grown past its size cap.
"""

from __future__ import annotations

import json
from typing import Any


def _git_out(repo: str, *args: str) -> str:
    import shutil
    import subprocess
    import sys

    if not shutil.which("git"):
        return ""
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _plugin_dir_matches(folder: Any, plugin: str) -> bool:
    """``folder`` is this plugin: it has a plugin.json whose id (or the folder's own name) is ``plugin``."""
    manifest = folder / "plugin.json"
    if not manifest.is_file():
        return False
    if folder.name == plugin:
        return True
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("id") or "") == plugin
    except (OSError, ValueError, AttributeError):
        return False


def resolve_plugin_repo(plugin: str, *, roots: list[str] | None = None, drafts: Any = None) -> dict[str, Any]:
    """Where plugin ``plugin`` (its id or folder name) lives as a git repo: a Ducky project that is
    the plugin, or holds it as ``<id>/`` or ``uefn-plugins/<id>/``, then its ai_plugins draft.
    A folder with git and an origin wins over one without."""
    from pathlib import Path

    pid = (plugin or "").strip().strip("/\\")
    if not pid:
        return {"ok": False, "error": "plugin is required (its id, e.g. island-media-kit)"}
    if roots is None:
        from backend.workspace.lanes import project_roots

        roots = project_roots()
    if drafts is None:
        try:
            from backend.uefn_plugins.store import appdata_ai_plugins_dir

            drafts = appdata_ai_plugins_dir()
        except Exception:
            drafts = None
    candidates: list[tuple[Path, str]] = []
    for root in roots:
        base = Path(root)
        for folder in (base, base / pid, base / "uefn-plugins" / pid):
            candidates.append((folder, "project"))
        for folder in sorted(base.glob("uefn-plugins/*")) if (base / "uefn-plugins").is_dir() else ():
            candidates.append((folder, "project"))
    if drafts is not None:
        candidates.append((Path(drafts) / pid, "ai_plugins"))
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for folder, source in candidates:
        key = str(folder.resolve()).lower() if folder.exists() else ""
        if not key or key in seen or not _plugin_dir_matches(folder, pid):
            continue
        seen.add(key)
        top = _git_out(str(folder), "rev-parse", "--show-toplevel")
        git = bool(top) and Path(top).resolve() == folder.resolve()
        origin = _git_out(str(folder), "remote", "get-url", "origin") if git else ""
        branch = _git_out(str(folder), "rev-parse", "--abbrev-ref", "HEAD") if git else ""
        found.append({"repo": folder.as_posix(), "source": source, "git": git, "origin": origin, "branch": branch})
    if not found:
        where = ", ".join(roots) or "no projects"
        return {"ok": False, "error": f"No repo for plugin '{pid}': no folder with its plugin.json in your Ducky projects "
                                      f"({where}) or in ai_plugins/{pid}. Clone it into a project or add its folder as a project."}
    found.sort(key=lambda r: (not (r["git"] and r["origin"]), not r["git"]))
    best = found[0]
    if not best["git"]:
        return {"ok": False, "error": f"Plugin '{pid}' is at {best['repo']} but that folder isn't a git repo. "
                                      "git init it and add its origin, then run again.", "repo": best["repo"]}
    label = f"(git, origin {best['branch']})" if best["origin"] else "(git, no origin)"
    return {"ok": True, **best, "summary": f"repo: {best['repo']} {label}", "others": [r["repo"] for r in found[1:]]}
