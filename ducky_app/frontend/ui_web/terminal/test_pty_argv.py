"""Command spawn must not require Git Bash."""

from frontend.ui_web.terminal.session import pty_argv


def test_command_argv_skips_shell_resolve():
    argv = pty_argv("bash", [r"C:\claude.exe", "auth", "login"])
    assert argv == [r"C:\claude.exe", "auth", "login"]


def test_an_agents_powershell_starts_light_and_the_persons_keeps_its_profile(monkeypatch):
    from frontend.ui_web.terminal import shells

    monkeypatch.setattr(shells.shutil, "which", lambda name: r"C:\ps\powershell.exe")
    mine = pty_argv("powershell", None)
    agents = pty_argv("powershell", None, agent=True)
    assert mine == [r"C:\ps\powershell.exe", "-NoLogo", "-NoExit"]
    assert agents[:3] == mine and "-NoProfile" in agents
    assert "Remove-Module PSReadLine -ErrorAction SilentlyContinue" in agents


if __name__ == "__main__":
    test_command_argv_skips_shell_resolve()
    print("ok")
