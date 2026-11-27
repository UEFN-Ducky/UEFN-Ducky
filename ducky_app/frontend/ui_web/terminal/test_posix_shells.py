"""Linux shell choice: $SHELL first, then bash; zsh, fish and pwsh when installed; agents get bash."""

import pytest

from frontend.ui_web.terminal import shells

_ALL = {"bash": "/usr/bin/bash", "zsh": "/usr/bin/zsh", "fish": "/usr/bin/fish", "pwsh": "/opt/microsoft/powershell/7/pwsh"}


def _linux(monkeypatch, installed, login_shell=""):
    monkeypatch.setattr(shells, "_posix", lambda: True)
    monkeypatch.setattr(shells.sys, "platform", "linux")  # macOS falls back to zsh, not bash
    monkeypatch.setattr(shells.shutil, "which", lambda name: _ALL[name] if name in installed else None)
    is_file = shells.Path.is_file  # a Mac has /bin/zsh and /bin/bash: only `installed` counts
    monkeypatch.setattr(shells.Path, "is_file", lambda self: not self.as_posix().startswith("/bin/") and is_file(self))
    monkeypatch.setenv("SHELL", login_shell)


@pytest.mark.parametrize("asked,label,argv", [
    ("bash", "bash", ["/usr/bin/bash"]),
    ("zsh", "zsh", ["/usr/bin/zsh"]),
    ("fish", "fish", ["/usr/bin/fish"]),
    ("pwsh", "powershell", ["/opt/microsoft/powershell/7/pwsh", "-NoLogo", "-NoExit"]),
    ("powershell", "powershell", ["/opt/microsoft/powershell/7/pwsh", "-NoLogo", "-NoExit"]),
])
def test_each_installed_shell_resolves(monkeypatch, asked, label, argv):
    _linux(monkeypatch, set(_ALL))
    assert shells.shell_label(asked) == label
    assert shells.resolve_shell(asked) == (argv[0], argv)


def test_the_default_is_the_login_shell_then_bash(monkeypatch):
    _linux(monkeypatch, set(_ALL), "/usr/bin/zsh")
    assert shells.shell_label("") == "zsh"
    _linux(monkeypatch, {"bash"}, "/usr/bin/zsh")  # $SHELL not installed
    assert shells.shell_label("") == "bash"
    _linux(monkeypatch, {"bash"}, "/usr/bin/tcsh")  # one we don't drive
    assert shells.shell_label("") == "bash"


def test_bin_bash_when_bash_is_not_on_path(monkeypatch):
    _linux(monkeypatch, set())
    monkeypatch.setattr(shells.Path, "is_file", lambda self: self.as_posix() == "/bin/bash")
    assert shells.resolve_shell("bash") == ("/bin/bash", ["/bin/bash"])


def test_a_missing_shell_is_an_error(monkeypatch):
    _linux(monkeypatch, {"bash"})
    with pytest.raises(FileNotFoundError, match="pwsh"):
        shells.resolve_shell("pwsh")


def test_the_list_offers_what_is_installed(monkeypatch):
    _linux(monkeypatch, {"bash", "zsh", "pwsh"})
    assert shells.available_shells() == ["bash", "zsh", "powershell"]
    _linux(monkeypatch, set(_ALL))
    assert shells.available_shells() == ["bash", "zsh", "fish", "powershell"]


def test_agents_get_light_bash(monkeypatch):
    _linux(monkeypatch, set(_ALL), "/usr/bin/fish")
    assert [shells.agent_shell(s) for s in ("", "bash", "zsh", "fish")] == ["bash"] * 4
    assert shells.agent_shell("pwsh") == "powershell"
    assert shells.resolve_shell("bash", agent=True)[1] == ["/usr/bin/bash", "--noprofile", "--norc"]
    assert shells.resolve_shell("zsh", agent=True)[1] == ["/usr/bin/zsh", "-f"]
    assert shells.resolve_shell("fish", agent=True)[1] == ["/usr/bin/fish", "--no-config"]
    _linux(monkeypatch, {"bash"})
    assert shells.agent_shell("pwsh") == "bash"  # asked for pwsh, none installed
