"""ducky_terminal_run refuses pipes that keep a command's output from the person."""

from backend.tools.panel.ducky_panel_terminal import hides_output


def test_buffering_pipes_are_refused() -> None:
    for command in (
        "py -3 -m pytest -q 2>&1 | Select-Object -Last 15",
        "npm test | select -first 5",
        "make | tail -n 20",
        "build.ps1 | Out-Null",
    ):
        assert "hides the output" in hides_output(command), command


def test_plain_commands_run() -> None:
    for command in ("py -3 -m pytest -q", "git status --short", "echo a || echo b", "Get-Date | Format-List"):
        assert hides_output(command) == "", command
