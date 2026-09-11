"""Command spawn must not require Git Bash."""

from frontend.ui_web.terminal.session import pty_argv


def test_command_argv_skips_shell_resolve():
    argv = pty_argv("bash", [r"C:\claude.exe", "auth", "login"])
    assert argv == [r"C:\claude.exe", "auth", "login"]


if __name__ == "__main__":
    test_command_argv_skips_shell_resolve()
    print("ok")
