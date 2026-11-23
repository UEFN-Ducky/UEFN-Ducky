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

    windows = mcp_inject.bootstrap_system_prompt(project_root="", conv_id="c1")
    monkeypatch.setattr(platform_text, "IS_LINUX", True)
    parts = {"mcp": "", "skill": "", "memory": "", "runtime": "Run py -3 here.", "rules": AGENT_HARD_RULES}
    for text in (hard_rules.hard_rules_for(""), mcp_inject.bootstrap_system_prompt(project_root="", conv_id="c1"),
                 assemble_system_prompt(parts)):
        assert "py -3" not in text and "powershell" not in text.lower()
    assert "py -3" in windows  # Windows keeps its text
