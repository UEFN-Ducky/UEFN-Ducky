"""Linux login PATH: fake claude/codex/gemini/npx/uvx in nvm, ~/.local/bin and ~/.npm-global."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.mcp_plugins import login_path as lp

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX PATH lookup")


def _fake(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / name
    exe.write_text(f"#!/bin/sh\necho {name} \"$@\"\n")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("NVM_DIR", raising=False)
    _fake(tmp_path / ".local/bin", "claude")
    _fake(tmp_path / ".npm-global/bin", "gemini")
    _fake(tmp_path / ".nvm/versions/node/v18.2.0/bin", "npx")
    _fake(tmp_path / ".nvm/versions/node/v20.11.1/bin", "npx")
    _fake(tmp_path / ".local/bin", "uvx")
    _fake(tmp_path / ".local/bin", "codex")
    monkeypatch.setattr(lp.Path, "home", lambda: tmp_path)
    return tmp_path


def test_finds_every_cli_on_a_bare_path(home):
    bare = "/nonexistent"
    for name in ("claude", "codex", "gemini", "uvx"):
        assert lp.which(name, bare).startswith(str(home)), name
    # newest nvm node wins
    assert lp.which("npx", bare) == str(home / ".nvm/versions/node/v20.11.1/bin/npx")


def test_login_path_keeps_order_and_adds_once(home):
    once = lp.login_path("/a:/b")
    assert once.startswith("/a:/b:") and lp.login_path(once) == once


def test_stdio_block_unwraps_cmd_and_spawns_argv(home):
    block = {"type": "stdio", "command": "cmd", "args": ["/c", "uvx", "demo mcp", "--x=$HOME"], "env": {"K": "v", "PATH": "/nonexistent"}}
    out = lp.posix_stdio_block(block)
    assert out["command"] == str(home / ".local/bin/uvx")
    assert out["args"] == ["demo mcp", "--x=$HOME"] and out["env"]["K"] == "v"
    # POSIX argv, no shell: spaces and $ reach the program untouched.
    run = subprocess.run([out["command"], *out["args"]], env={**os.environ, **out["env"]},
                         capture_output=True, text=True, check=True)
    assert run.stdout.strip() == "uvx demo mcp --x=$HOME"


def test_stdio_block_drops_windows_suffix(home):
    out = lp.posix_stdio_block({"command": "npx.cmd", "args": ["-y", "tool"], "env": {"PATH": "/nonexistent"}})
    assert out["command"].endswith("/v20.11.1/bin/npx") and out["args"] == ["-y", "tool"]


def test_runner_resolves_agent_cli(home):
    from backend.agent.coding_agents.runner import linux_cli

    env = {"PATH": "/nonexistent"}
    assert linux_cli("claude_code", "", env) == str(home / ".local/bin/claude")
    assert str(home / ".npm-global/bin") in env["PATH"]
    assert linux_cli("gemini", "/opt/my/gemini", env) == "/opt/my/gemini"
