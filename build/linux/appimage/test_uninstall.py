"""AppRun --install-integration / --uninstall [--purge] in a throwaway HOME."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_appimage as ba  # noqa: E402


def _bash() -> str | None:
    if os.name == "nt":  # Git Bash, never WSL's System32\bash.exe
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")


@pytest.fixture
def appdir(tmp_path: Path) -> Path:
    app = tmp_path / "build" / "uefn-ducky"
    (app / "_internal").mkdir(parents=True)
    for name in ("uefn-ducky", "uefn-ducky-bridge", "Ducky-Launcher", "_internal/libportaudio.so.2"):
        (app / name).write_bytes(b"\x7fELF")
    try:
        return ba.assemble_appdir(app, tmp_path / "AppDir")
    except OSError:
        pytest.skip("symlinks unavailable")


def _run(appdir: Path, home: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), APPIMAGE=str(home / "Apps" / "UEFN Ducky.AppImage"))
    # No desktop tools: the result must not depend on what the runner has installed.
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if not any((Path(p) / t).exists() for t in ("xdg-mime", "update-desktop-database")))
    proc = subprocess.run([BASH, str(appdir / "AppRun"), *args], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _tree(home: Path) -> list[str]:
    return sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    chats = home / ".local" / "share" / "UEFN-Ducky" / "chats"
    chats.mkdir(parents=True)
    (chats / "keep.json").write_text("{}", encoding="utf-8")
    (home / ".cache" / "UEFN-Ducky").mkdir(parents=True)
    (home / ".cache" / "UEFN-Ducky" / "search.bin").write_bytes(b"x")
    # What the app adds itself: autostart (frontend/autostart.py) and the link handler (xdg-mime).
    (home / ".config" / "autostart").mkdir(parents=True)
    (home / ".config" / "autostart" / "uefn-ducky.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    (home / ".config" / "mimeapps.list").write_text(
        "[Default Applications]\nx-scheme-handler/uefn-ducky=uefn-ducky.desktop\ntext/html=firefox.desktop\n",
        encoding="utf-8")
    return home


def test_install_then_uninstall_keeps_only_user_data(appdir: Path, tmp_path: Path) -> None:
    home = _home(tmp_path)
    _run(appdir, home, "--install-integration")
    entry = home / ".local" / "share" / "applications" / "uefn-ducky.desktop"
    text = entry.read_text(encoding="utf-8")
    # Same Exec line the app writes on its next start (no churn between the two).
    sys.path.insert(0, str(HERE.parents[2] / "ducky_app"))
    from frontend.open_files import desktop_exec_quote

    assert f"Exec={desktop_exec_quote(str(home / 'Apps' / 'UEFN Ducky.AppImage'))} %U\n" in text
    assert (home / ".local/share/icons/hicolor/256x256/apps/uefn-ducky.png").is_file()

    out = _run(appdir, home, "--uninstall")
    assert "Chats and settings stay" in out
    assert _tree(home) == [
        ".cache", ".config", ".config/mimeapps.list",
        ".local", ".local/share", ".local/share/UEFN-Ducky",
        ".local/share/UEFN-Ducky/chats", ".local/share/UEFN-Ducky/chats/keep.json",
    ]
    # Only Ducky's handler line goes; the person's other defaults stay.
    assert (home / ".config" / "mimeapps.list").read_text(encoding="utf-8") == (
        "[Default Applications]\ntext/html=firefox.desktop\n")


def test_purge_also_deletes_chats_settings_and_caches(appdir: Path, tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / ".config" / "UEFN-Ducky").mkdir()
    _run(appdir, home, "--install-integration")
    _run(appdir, home, "--uninstall", "--purge")
    assert _tree(home) == [".cache", ".config", ".config/mimeapps.list", ".local", ".local/share"]


def test_uninstall_without_integration_is_harmless(appdir: Path, tmp_path: Path) -> None:
    home = tmp_path / "empty"
    home.mkdir()
    _run(appdir, home, "--uninstall")
    assert _tree(home) == []
