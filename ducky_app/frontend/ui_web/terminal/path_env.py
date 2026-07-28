"""Refresh Windows PATH for child shells (User PATH changes after Ducky started)."""

from __future__ import annotations

import os


def windows_merged_path() -> str:
    """Machine + User Path from the registry (not the stale process env)."""
    if os.name != "nt":
        return os.environ.get("Path") or os.environ.get("PATH") or ""
    try:
        import winreg

        def _read(root: int, subkey: str) -> str:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    return str(val or "")
            except OSError:
                return ""

        machine = _read(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        user = _read(winreg.HKEY_CURRENT_USER, r"Environment")
        parts: list[str] = []
        seen: set[str] = set()
        for chunk in (machine, user):
            for part in chunk.split(";"):
                p = part.strip()
                if not p:
                    continue
                key = p.lower()
                if key in seen:
                    continue
                seen.add(key)
                parts.append(p)
        return ";".join(parts)
    except Exception:
        return os.environ.get("Path") or os.environ.get("PATH") or ""


def env_with_fresh_path(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copy env and replace Path with the current User+Machine PATH."""
    env = dict(base if base is not None else os.environ)
    merged = windows_merged_path()
    if merged:
        env["Path"] = merged
        env["PATH"] = merged
    # Claude Code / OAuth: embedded ConPTY often fails to open the default
    # browser ("about" link). Point BROWSER at a real Chrome/Edge if present.
    if not (env.get("BROWSER") or "").strip():
        browser = _windows_browser_exe()
        if browser:
            env["BROWSER"] = browser
    return env


def _windows_browser_exe() -> str:
    if os.name != "nt":
        return ""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe") if local else "",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def refresh_process_path() -> str:
    """Update this process's Path so which()/child tools see new installs."""
    merged = windows_merged_path()
    if merged:
        os.environ["Path"] = merged
        os.environ["PATH"] = merged
    return merged
