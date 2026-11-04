"""The stub Claude CLI speaks Claude Code's protocol: the anthropic plugin's own probe
helpers and stream parser accept it, and its tool calls reach a real MCP server."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

STUB = Path(__file__).with_name("stub_claude.py")
PLUGIN = Path(__file__).resolve().parents[3].parent / "uefn-plugins" / "uefn-plugin-anthropic"
if not (PLUGIN / "backend" / "claude_code_adapter.py").is_file():
    pytest.skip(f"anthropic plugin repo not found at {PLUGIN}", allow_module_level=True)

_pkg = types.ModuleType("_stub_claude_plugin")
_pkg.__path__ = [str(PLUGIN / "backend")]
sys.modules[_pkg.__name__] = _pkg
adapter = importlib.import_module(_pkg.__name__ + ".claude_code_adapter")
claude_auth = importlib.import_module(_pkg.__name__ + ".claude_auth")
cli_update = importlib.import_module(_pkg.__name__ + ".cli_update")

FAKE_MCP = textwrap.dedent("""
    import json, os, sys, time
    log = open(sys.argv[1], "a", encoding="utf-8")
    TOOLS = ["ducky_get_plan", "workspace_read_file", "workspace_search", "ducky_terminal_open",
             "ducky_terminal_run", "workspace_write_file", "ducky_ask_user"]
    for raw in sys.stdin:
        msg = json.loads(raw)
        method, rid = msg.get("method"), msg.get("id")
        log.write(json.dumps({"method": method, "params": msg.get("params")}) + "\\n"); log.flush()
        if rid is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "uefn", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": t, "inputSchema": {"type": "object"}} for t in TOOLS]}
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "ducky_terminal_run" and "sleep(999)" in msg["params"]["arguments"].get("command", ""):
                time.sleep(30)
            text = json.dumps({"ok": True, "session_id": "term-1"} if name == "ducky_terminal_open" else {"ok": True, "tool": name})
            if name == "ducky_terminal_run" and os.environ.get("FAKE_INJECT"):
                text += "\\n[message from the person] " + os.environ["FAKE_INJECT"]
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n"); sys.stdout.flush()
""")


@pytest.fixture
def world(tmp_path):
    server = tmp_path / "fake_mcp.py"
    server.write_text(FAKE_MCP, encoding="utf-8")
    log = tmp_path / "mcp_calls.jsonl"
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"uefn": {
        "command": sys.executable, "args": [str(server), str(log)], "env": {"FAKE": "1"}}}}), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(5):
        (repo / f"file{i}.txt").write_text(f"stub fixture {i}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {**os.environ, "STUB_SANDBOX": "1", "STUB_FIXTURE_REPO": str(repo), "STUB_TURN_S": "2.5",
           "STUB_GAP_S": "0.01-0.03", "STUB_TOKENS_PER_S": "200000"}
    argv = adapter.build_claude_argv(
        binary=sys.executable, prompt="", system_prompt="", model="claude-haiku-5-5",
        mcp_config_path=str(config), extra_args="", session_id="", permission_mode="acceptEdits",
        image_dirs=[str(tmp_path)], system_prompt_file=str(tmp_path / "sys.txt"), prompt_via_stdin=True)
    argv = [sys.executable, str(STUB), *argv[1:], "--input-format", "stream-json", "--setting-sources", "",
            "--some-future-flag"]
    return types.SimpleNamespace(tmp=tmp_path, env=env, argv=argv, log=log, repo=repo, config=config)


def _user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"


def _parse(lines: list[str]):
    events: list[dict] = []
    state = adapter._StreamState("conv-stub", "run-1", events.append, require_ducky=True)
    for line in lines:
        state.on_line(line)
    state.flush_stream()
    return state, events


def test_argv_parse_takes_the_adapters_flags(world):
    opts = sys.modules.setdefault("stub_claude", _load_stub()).parse_argv(world.argv[2:] + ["--resume", "sess-9"])
    assert opts["print"] and opts["output_format"] == "stream-json" and opts["input_format"] == "stream-json"
    assert opts["mcp_config"] == str(world.config) and opts["model"] == "claude-haiku-5-5"
    assert opts["resume"] == "sess-9" and opts["tools"] and opts["permission_mode"] == "acceptEdits"
    assert opts["add_dir"] == [str(world.tmp)] and opts["positional"] == []


def _load_stub():
    spec = importlib.util.spec_from_file_location("stub_claude", STUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_swarm_turn_parses_with_the_adapters_stream_parser(world):
    proc = subprocess.run(world.argv, input=_user_line("Build the thing"), capture_output=True, text=True,
                          encoding="utf-8", env=world.env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    state, events = _parse(lines)
    assert state.ducky_connected and not state.ducky_connection_failed
    assert state.session_id and not state.is_error and state.final_text == "Done."
    assert state.usage["output_tokens"] > 0 and state.usage["cost_usd"] == 0
    kinds = [e["type"] for e in events]
    assert kinds.count("tool") == kinds.count("tool_done") >= 5
    assert "text_delta" in kinds
    shown = [e["tool"]["name"] for e in events if e["type"] == "tool"]
    assert {"mcp__uefn__ducky_get_plan", "mcp__uefn__workspace_read_file", "mcp__uefn__workspace_search",
            "mcp__uefn__ducky_terminal_run", "mcp__uefn__workspace_write_file"} <= set(shown)
    assert shown.count("mcp__uefn__ducky_ask_user") == 1
    assert all(e["success"] for e in events if e["type"] == "tool_done")
    result = json.loads(lines[-1])
    assert result["type"] == "result" and result["subtype"] == "success" and result["total_cost_usd"] == 0
    assert result["stub_counts"]["tool_calls"] == len(shown) and result["stub_counts"]["ask_user"] == 1
    # The calls really went over MCP, in order: handshake, catalog, then the tools.
    calls = [json.loads(x) for x in world.log.read_text(encoding="utf-8").splitlines()]
    assert [c["method"] for c in calls[:3]] == ["initialize", "notifications/initialized", "tools/list"]
    called = [c["params"]["name"] for c in calls if c["method"] == "tools/call"]
    assert len(called) == len(shown)
    run = next(c["params"]["arguments"] for c in calls if c["method"] == "tools/call"
               and c["params"]["name"] == "ducky_terminal_run")
    assert run["session_id"] == "term-1" and "time.sleep(30)" in run["command"]


def test_a_control_request_interrupt_ends_the_turn_marked_interrupted(world):
    env = {**world.env, "STUB_SCENARIO": "interrupt", "STUB_TURN_S": "120", "STUB_COMMAND_S": "999"}
    proc = subprocess.Popen(world.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", env=env)
    try:
        proc.stdin.write(_user_line("Long job"))
        proc.stdin.flush()
        # The fake server holds the 999 s command for 30 s: the interrupt lands mid-tool.
        lines = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            lines.append(line)
            if '"mcp__uefn__ducky_terminal_run"' in line:
                break
        assert '"mcp__uefn__ducky_terminal_run"' in lines[-1]
        proc.stdin.write(json.dumps({"type": "control_request", "request_id": "r1",
                                     "request": {"subtype": "interrupt"}}) + "\n")
        proc.stdin.flush()
        rest, _ = proc.communicate(timeout=15)
    finally:
        proc.kill()
    lines += rest.splitlines()
    parsed = [json.loads(x) for x in lines if x.strip()]
    assert any(p["type"] == "control_response" and p["response"]["request_id"] == "r1" for p in parsed)
    result = parsed[-1]
    assert result["type"] == "result" and result["interrupted"] is True
    state, _ = _parse(lines)
    assert state.is_error and state.session_id


def test_interrupt_scenario_commits_in_the_fixture_repo(world):
    env = {**world.env, "STUB_SCENARIO": "interrupt", "STUB_TURN_S": "3", "STUB_COMMAND_S": "1"}
    proc = subprocess.run(world.argv, input=_user_line("Commit"), capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    log = subprocess.run(["git", "-C", str(world.repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "stub commit" in log


def test_interrupt_scenario_logs_phases_and_where_messages_landed(world):
    log = world.tmp / "stub_log.jsonl"
    env = {**world.env, "STUB_SCENARIO": "interrupt", "STUB_TURN_S": "30", "STUB_COMMAND_S": "60",
           "STUB_LOG": str(log), "FAKE_INJECT": "SOAKMSG-7: hello", "DUCKY_CONV_ID": "conv-abc"}
    proc = subprocess.run(world.argv, input=_user_line("SOAKMSG-1: start"), capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert all(e["conv"] == "conv-abc" for e in events)
    assert [e["phase"] for e in events if e["kind"] == "phase"] == ["streaming", "command", "git", "ask", "approval"]
    got = {(e["id"], e["path"]) for e in events if e["kind"] == "message"}
    # The 60 s command came back at once with the message in it: an early return.
    assert got == {("SOAKMSG-1", "turn"), ("SOAKMSG-7", "early return + steer")}
    calls = [json.loads(x) for x in world.log.read_text(encoding="utf-8").splitlines()]
    run = next(c["params"]["arguments"] for c in calls if c.get("method") == "tools/call"
               and c["params"]["name"] == "ducky_terminal_run")
    assert "expect_s" not in run  # the stub's own marker never reaches the tool
    assert any(c["params"]["name"] == "ducky_permission_prompt" for c in calls if c.get("method") == "tools/call")


def test_plain_text_stdin(world):
    at = world.argv.index("--input-format")
    argv = world.argv[:at] + world.argv[at + 2:]
    proc = subprocess.run(argv, input="Plain prompt text", capture_output=True, text=True, encoding="utf-8",
                          env={**world.env, "STUB_TURN_S": "0.5"}, timeout=60)
    assert proc.returncode == 0, proc.stderr
    state, _ = _parse(proc.stdout.splitlines())
    assert state.ducky_connected and not state.is_error


def test_probes_answer_what_the_plugin_expects(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_SANDBOX", "1")
    # resolve_claude_bin swaps a .cmd for ~/.local/bin/claude.exe when that exists: with the
    # real home it ran the person's real Claude. The soak's sandbox home has none either.
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    launcher = tmp_path / "claude.cmd"
    launcher.write_text(f'@"{sys.executable}" "{STUB}" %*\r\n', encoding="utf-8")
    assert cli_update.read_cli_version(str(launcher)) == "2.1.999"
    status = claude_auth.claude_auth_status(str(launcher))
    assert status == {"loggedIn": True, "authMethod": "stub"}
    one_shot = subprocess.run([str(launcher), "-p", "--output-format", "text", "--model", "x"], input="hello there",
                              capture_output=True, text=True, timeout=30)
    assert one_shot.returncode == 0 and "hello there" in one_shot.stdout


def test_refuses_outside_a_sandbox(world):
    env = {k: v for k, v in world.env.items() if k != "STUB_SANDBOX"}
    proc = subprocess.run([sys.executable, str(STUB), "--version"], capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 2 and "STUB_SANDBOX" in proc.stderr


def test_memory_knob_holds_the_memory(world):
    env = {**world.env, "STUB_MEMORY_MB": "64", "STUB_TURN_S": "60", "STUB_GAP_S": "60-60"}
    proc = subprocess.Popen(world.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    try:
        proc.stdin.write(_user_line("hold"))
        proc.stdin.flush()
        assert '"init"' in proc.stdout.readline()
        psutil = pytest.importorskip("psutil")
        root = psutil.Process(proc.pid)  # a venv python.exe is a redirector: the stub is its child
        assert max(p.memory_info().rss for p in [root, *root.children(recursive=True)]) > 64 * 1024 * 1024
    finally:
        proc.kill()
        proc.wait(10)


def test_sleep_command_runs_the_stubs_own_python_in_powershell():
    spec = importlib.util.spec_from_file_location("_stub_for_sleep", STUB)
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    command = stub.sleep_command(0.2)
    assert sys.executable in command and not command.startswith("python")
    started = time.monotonic()
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert time.monotonic() - started >= 0.2
