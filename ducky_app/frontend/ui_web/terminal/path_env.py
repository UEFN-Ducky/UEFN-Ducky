"""Refresh Windows PATH for child shells (User PATH changes after Ducky started)."""

from __future__ import annotations

import os
import subprocess
import sys

# Linux/macOS: the login shell's PATH, read once (None = not read yet).
# macOS: Homebrew's folders (Apple Silicon, Intel); a GUI app starts with a bare PATH.
_MAC_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")
_login_path: str | None = None
_PATH_MARK = "__DUCKY_PATH__"


def posix_login_path(shell: str = "bash") -> str:
    """PATH as the person's interactive login bash sets it ('' when it can't say), read once.

    A desktop app starts with the session's bare PATH; nvm, ~/.local/bin and pyenv live in
    the shell's rc files, and nvm's lines sit after .bashrc's interactive-only guard, hence
    -i. The marks fence the value off from anything the rc files print.
    """
    global _login_path
    if _login_path is None:
        value = ""
        try:
            out = subprocess.run(
                [shell, "-ilc", f'printf "{_PATH_MARK}%s{_PATH_MARK}" "$PATH"'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                # Its own session: an interactive bash takes over a controlling terminal it
                # shares, and Ducky (then a background job on it) was stopped by SIGTTOU.
                start_new_session=True,
            )
            parts = out.stdout.split(_PATH_MARK)
            value = parts[-2] if len(parts) >= 3 else ""
        except (OSError, subprocess.SubprocessError):
            pass
        _login_path = value
    return _login_path


def _login_shell() -> str:
    """The shell whose rc files set PATH: bash on Linux; $SHELL or zsh on macOS."""
    if sys.platform != "darwin":
        return "bash"
    return os.environ.get("SHELL") or "/bin/zsh"


def _posix_merged_path() -> str:
    """The login shell's PATH first, then this process's entries it lacks (macOS: then Homebrew's)."""
    parts: list[str] = []
    extra = os.pathsep.join(_MAC_EXTRA_PATH) if sys.platform == "darwin" else ""
    for chunk in (posix_login_path(_login_shell()), os.environ.get("PATH", ""), extra):
        for part in chunk.split(os.pathsep):
            if part and part not in parts:
                parts.append(part)
    return os.pathsep.join(parts)


def windows_merged_path() -> str:
    """Machine + User Path from the registry (not the stale process env)."""
    if os.name != "nt":
        return _posix_merged_path()
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


def _set_path(env: dict[str, str], value: str) -> None:
    """One PATH entry, whatever casing the copy had.

    Oct 22 2026: both "Path" and "PATH" went into the terminal's environment block, and Git
    then couldn't start its credential helper: every `git push` in a Ducky terminal died with
    exit 128 and no message, while the same push worked in Codex, Cursor or a normal console.
    """
    for key in [k for k in env if k.lower() == "path"]:
        del env[key]
    env["PATH"] = value


def env_with_fresh_path(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copy env and replace Path with the current User+Machine PATH."""
    env = dict(base if base is not None else os.environ)
    merged = windows_merged_path()
    if merged:
        _set_path(env, merged)
    _add_ducky_ffmpeg(env)
    # Claude Code / OAuth: embedded ConPTY often fails to open the default
    # browser ("about" link). Point BROWSER at a real Chrome/Edge if present.
    if not (env.get("BROWSER") or "").strip():
        browser = _windows_browser_exe()
        if browser:
            env["BROWSER"] = browser
    return env


def _add_ducky_ffmpeg(env: dict[str, str]) -> None:
    """Ducky's own ffmpeg for programs run in its terminals.

    Oct 12 2026: a plugin's tests ran in a terminal, outside the app, and could not find the
    encoder the app bundles, so no builder could make its test export. ``DUCKY_FFMPEG`` and
    ``DUCKY_FFPROBE`` name the exact files; the folder goes last on PATH, so the person's own
    ffmpeg still wins.
    """
    try:
        from backend.agent.video.ffmpeg_install import binaries

        pair = binaries()
    except Exception:
        pair = None
    if not pair:
        return
    ffmpeg, ffprobe = (str(p) for p in pair)
    env["DUCKY_FFMPEG"], env["DUCKY_FFPROBE"] = ffmpeg, ffprobe
    folder = os.path.dirname(ffmpeg)
    current = next((v for k, v in env.items() if k.lower() == "path"), "")
    if folder.lower() not in (part.strip().lower() for part in current.split(os.pathsep)):
        _set_path(env, f"{current}{os.pathsep}{folder}" if current else folder)


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
        os.environ["PATH"] = merged  # one key on Windows: os.environ ignores the casing
    return merged
