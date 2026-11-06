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


def _git_bash(monkeypatch, path: str) -> None:
    from frontend.ui_web.terminal import shells

    monkeypatch.setattr(shells, "_git_bash", lambda: path)


def test_no_git_bash_gives_an_agent_light_powershell_with_no_error(monkeypatch):
    from frontend.ui_web.terminal.shells import agent_shell

    _git_bash(monkeypatch, "")
    assert agent_shell("bash") == "powershell"
    assert agent_shell("") == "powershell"


def test_git_bash_present_gives_an_agent_light_bash_and_the_person_keeps_theirs(monkeypatch):
    from frontend.ui_web.terminal.shells import agent_shell

    _git_bash(monkeypatch, r"C:\Git\bin\bash.exe")
    assert agent_shell("bash") == "bash"
    assert pty_argv("bash", None, agent=True) == [r"C:\Git\bin\bash.exe", "--login", "--noprofile", "--norc"]
    assert pty_argv("bash", None) == [r"C:\Git\bin\bash.exe", "--login"]  # the person's own


def test_an_agent_that_asks_for_powershell_gets_it(monkeypatch):
    from frontend.ui_web.terminal.shells import agent_shell

    _git_bash(monkeypatch, r"C:\Git\bin\bash.exe")
    assert agent_shell("powershell") == "powershell" and agent_shell("pwsh") == "powershell"


def test_the_manager_swaps_a_missing_bash_for_an_agent_only(monkeypatch, tmp_path):
    from frontend.ui_web.terminal import manager as manager_module
    from frontend.ui_web.terminal.test_chat_terminal_cap import _Bridge

    _git_bash(monkeypatch, "")
    started: list[str] = []
    monkeypatch.setattr(manager_module.TerminalSession, "spawn", lambda self: started.append(self.shell))
    monkeypatch.setattr(manager_module, "TerminalBridge", _Bridge)
    monkeypatch.setattr(manager_module.TerminalManager, "_ensure_reaper", lambda self: None)
    monkeypatch.setattr(manager_module.TerminalManager, "ensure_budget_ticker", lambda self: None)
    mgr = manager_module.TerminalManager()
    agents = mgr.spawn(shell="bash", cwd=str(tmp_path), conv_id="chat-a")
    assert agents["ok"] and agents["shell_fallback"] is True and started == ["powershell"]
    mgr.spawn(shell="bash", cwd=str(tmp_path))  # the person's own: asks for bash as before
    assert started == ["powershell", "bash"]


if __name__ == "__main__":
    test_command_argv_skips_shell_resolve()
    print("ok")
