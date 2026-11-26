"""A prompt built on Linux has no `py -3` and no PowerShell; Windows text is untouched."""

from __future__ import annotations

from backend.agent.hard_rules import AGENT_HARD_RULES
from backend.agent.prompts.platform_text import for_platform


def test_windows_text_is_unchanged():
    assert for_platform(AGENT_HARD_RULES, linux=False) is AGENT_HARD_RULES


def test_linux_hard_rules_say_bash_and_python3():
    text = for_platform(AGENT_HARD_RULES, linux=True)
    low = text.lower()
    assert "py -3" not in text and "powershell" not in low and "%localappdata%" not in low
    assert "Select-Object" not in text and "Out-Null" not in text  # Windows no-pipe rule left out
    assert "Shell: bash, `python3`." in text and "~/.local/share/UEFN-Ducky/" in text
    # every other rule survives
    assert "**No windows on the person's screen (HARD):**" in text
    assert len(text) > 0.9 * len(AGENT_HARD_RULES)


def test_mac_hard_rules_say_macos_zsh_open_and_cmd():
    text = for_platform(AGENT_HARD_RULES, mac=True)
    low = text.lower()
    assert "py -3" not in text and "powershell" not in low and "%localappdata%" not in low
    assert "Select-Object" not in text
    assert "You are on macOS; the shell is zsh" in text and "`open -R`" in text and "Cmd" in text
    assert "~/Library/Application Support/UEFN-Ducky/" in text and "~/.local/share" not in text
    assert len(text) > 0.9 * len(AGENT_HARD_RULES)
    assert for_platform("Run: py -3 -m pytest in PowerShell", mac=True) == "Run: python3 -m pytest in zsh"


def test_the_os_flag_picks_mac(monkeypatch):
    from backend.agent.prompts import platform_text

    monkeypatch.setattr(platform_text, "IS_LINUX", False)
    monkeypatch.setattr(platform_text, "IS_MAC", True)
    assert "macOS" in for_platform("Windows shell: `py -3`.")
    monkeypatch.setattr(platform_text, "IS_MAC", False)
    assert for_platform("Windows shell: `py -3`.") == "Windows shell: `py -3`."


def test_linux_sample_command():
    assert for_platform("Run: py -3 -m pytest in PowerShell", linux=True) == "Run: python3 -m pytest in bash"


def test_coding_agent_bootstrap_text_on_linux():
    sample = ('`ducky_terminal_open` (shell "bash" or "powershell", cwd = the folder)\n'
              "working around it with PowerShell.\nnative WebSearch, the Browser tab, Bash, PowerShell, Glob")
    out = for_platform(sample, linux=True)
    assert "powershell" not in out.lower() and '(shell "bash",' in out and "Bash, Glob" in out


def test_every_prompt_an_agent_gets_goes_through_the_switch(monkeypatch):
    from backend.agent import hard_rules
    from backend.agent.coding_agents import mcp_inject
    from backend.agent.prompt import assemble_system_prompt
    from backend.agent.prompts import platform_text

    monkeypatch.setattr(platform_text, "IS_MAC", False)
    monkeypatch.setattr(platform_text, "IS_LINUX", False)
    windows = mcp_inject.bootstrap_system_prompt(project_root="", conv_id="c1")
    monkeypatch.setattr(platform_text, "IS_LINUX", True)
    linux = mcp_inject.bootstrap_system_prompt(project_root="", conv_id="c1")
    # The switch swaps words, never drops the terminal how-to.
    assert "`ducky_terminal_run(session_id, command)`" in linux and "no cat, less" in linux
    parts = {"mcp": "", "skill": "", "memory": "", "runtime": "Run py -3 here.", "rules": AGENT_HARD_RULES}
    for text in (hard_rules.hard_rules_for(""), mcp_inject.bootstrap_system_prompt(project_root="", conv_id="c1"),
                 assemble_system_prompt(parts)):
        assert "py -3" not in text and "powershell" not in text.lower()
    assert "py -3" in windows  # Windows keeps its text
