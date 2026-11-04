"""Stub Claude Code CLI for swarm soak runs: streams like Claude, costs nothing.

Answers the probes the anthropic plugin makes on the Claude binary (``--version``,
``auth status --json``, ``auth login/logout``, ``update``, a one-shot ``-p`` text
completion) and runs a stream-json turn that looks like Claude Code's own output:
``system/init`` (with the ``uefn`` MCP server connected), ``stream_event`` text
deltas, ``assistant`` messages with ``mcp__uefn__*`` tool_use blocks, ``user``
messages with their tool_result, and a final ``result``. The tool calls are real:
the stub starts the ``uefn`` server from ``--mcp-config`` and speaks MCP over stdio,
so Ducky does all the work it does for a real agent. No model is called.

Env:
  STUB_SANDBOX=1        required; the stub refuses to run without it
  STUB_SCENARIO         swarm (default) | interrupt
  STUB_TURN_S           turn length in seconds (default 600)
  STUB_GAP_S            "min-max" seconds between steps (default "3-8")
  STUB_TOKENS_PER_S     streaming speed (default 50); a step streams ~200 tokens
  STUB_FIXTURE_REPO     repo the tools read/search/write (default: the cwd)
  STUB_MEMORY_MB        allocate and hold this many MB for the whole run
  STUB_COMMAND_S        interrupt scenario: length of the long command (default 180)
  STUB_TERMINAL_S       swarm scenario: length of each terminal command (default 30)
  STUB_LOG              append one JSON line per turn start/end, tool call and message read

A ``control_request`` interrupt on stdin (with ``--input-format stream-json``) or
Ctrl+Break ends the turn with a ``result`` marked ``interrupted``.
"""

from __future__ import annotations

import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any

VERSION = "2.1.999"
_VALUE_FLAGS = frozenset({
    "--output-format", "--input-format", "--mcp-config", "--tools", "--allowedTools", "--allowed-tools",
    "--disallowedTools", "--disallowed-tools", "--permission-mode", "--permission-prompt-tool", "--resume",
    "--model", "--append-system-prompt-file", "--append-system-prompt", "--system-prompt", "--add-dir",
    "--setting-sources", "--session-id", "--fallback-model", "--max-turns", "--settings",
})
_WORDS = ("the plan step reads the file then checks its tests and writes the change "
          "before the next tool call lands in the fixture repo while the swarm keeps working").split()
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_MSG_RE = re.compile(r"SOAKMSG-\d+")


def log_event(kind: str, **fields: Any) -> None:
    """One line in STUB_LOG (the soak harness counts tool calls and delivered messages)."""
    path = os.environ.get("STUB_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "pid": os.getpid(), "kind": kind, **fields}) + "\n")
    except OSError:
        pass


def parse_argv(args: list[str]) -> dict[str, Any]:
    """Flags Claude Code takes for a turn; unknown flags are ignored."""
    opts: dict[str, Any] = {"add_dir": [], "positional": [], "print": False}
    i = 0
    while i < len(args):
        arg = args[i]
        name, eq, inline = arg.partition("=")
        if name in _VALUE_FLAGS:
            value = inline if eq else (args[i + 1] if i + 1 < len(args) else "")
            i += 1 if eq else 2
            key = name.lstrip("-").replace("-", "_")
            if key == "add_dir":
                opts["add_dir"].append(value)
            else:
                opts[key] = value
            continue
        if arg in ("-p", "--print"):
            opts["print"] = True
        elif not arg.startswith("-"):
            opts["positional"].append(arg)
        i += 1
    return opts


class Out:
    """stdout writer shared by the turn and the stdin reader."""

    def __init__(self) -> None:
        self.lock = threading.Lock()

    def line(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, ensure_ascii=False)
        with self.lock:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()


class Mcp:
    """Minimal MCP client over stdio (newline-delimited JSON-RPC)."""

    def __init__(self, server: dict[str, Any]) -> None:
        env = {**os.environ, **{str(k): str(v) for k, v in (server.get("env") or {}).items()}}
        self.proc = subprocess.Popen(
            [server["command"], *server.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, creationflags=_NO_WINDOW)
        self.next_id = 0
        self.lock = threading.Lock()

    def _send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.next_id += 1
            rid = self.next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            assert self.proc.stdout is not None
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    raise ConnectionError("MCP server closed its output")
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:  # server -> client request (ping, roots/list)
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"].get("message") if isinstance(msg["error"], dict) else msg["error"]))
                    return msg.get("result") or {}

    def notify(self, method: str) -> None:
        with self.lock:
            self._send({"jsonrpc": "2.0", "method": method})

    def start(self) -> list[str]:
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "claude-code-stub", "version": VERSION}})
        self.notify("notifications/initialized")
        return [str(t.get("name")) for t in self.request("tools/list", {}).get("tools") or []]

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


class Turn:
    def __init__(self, opts: dict[str, Any], out: Out) -> None:
        self.opts = opts
        self.out = out
        self.session_id = opts.get("resume") or opts.get("session_id") or str(uuid.uuid4())
        self.model = opts.get("model") or "claude-haiku-5-5"
        self.interrupted = threading.Event()
        self.prompts: Queue[str] = Queue()
        self.stdin_closed = threading.Event()
        self.steers: list[str] = []
        self.counts = {"text_deltas": 0, "tool_calls": 0, "ask_user": 0, "messages_read": 0}
        self.n = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}
        self.repo = Path(os.environ.get("STUB_FIXTURE_REPO") or os.getcwd())
        self.terminal_session = ""
        self.mcp: Mcp | None = None
        self.conv = os.environ.get("DUCKY_CONV_ID") or ""

    def log(self, kind: str, **fields: Any) -> None:
        log_event(kind, session=self.session_id, conv=self.conv, **fields)

    def saw_messages(self, text: str, path: str) -> None:
        """Log every soak message id (SOAKMSG-<n>) that reached this agent, and how."""
        for msg_id in sorted(set(_MSG_RE.findall(text or ""))):
            self.log("message", id=msg_id, path=path)

    # ── input ──────────────────────────────────────────────────────────

    def read_stdin(self) -> None:
        """stream-json input: user lines are prompts (later ones are steer messages)."""
        for raw in sys.stdin:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "control_request":
                request = msg.get("request") or {}
                self.out.line({"type": "control_response", "response": {
                    "subtype": "success", "request_id": msg.get("request_id"), "response": {}}})
                if request.get("subtype") == "interrupt":
                    self.interrupted.set()
                    break
            elif msg.get("type") == "user":
                content = (msg.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
                self.prompts.put(str(content or ""))
        self.stdin_closed.set()

    def first_prompt(self) -> str:
        if self.opts.get("input_format") != "stream-json":
            return " ".join(self.opts["positional"]) or sys.stdin.read()
        threading.Thread(target=self.read_stdin, name="stub-stdin", daemon=True).start()
        while not self.interrupted.is_set():
            try:
                return self.prompts.get(timeout=0.1)
            except Empty:
                if self.stdin_closed.is_set():
                    return ""
        return ""

    def take_steers(self) -> list[str]:
        got = []
        while True:
            try:
                got.append(self.prompts.get_nowait())
            except Empty:
                return got

    # ── output helpers ─────────────────────────────────────────────────

    def wait(self, seconds: float) -> bool:
        """Sleep; False once interrupted."""
        return not self.interrupted.wait(max(seconds, 0.0))

    def stream_text(self, text: str) -> bool:
        """Stream ``text`` as content_block deltas at STUB_TOKENS_PER_S."""
        tps = max(float(os.environ.get("STUB_TOKENS_PER_S") or 50), 1.0)
        words = text.split(" ")
        debt = 0.0  # sleeps under the OS timer tick (~15 ms on Windows) add up instead
        self.out.line({"type": "stream_event", "session_id": self.session_id, "event": {
            "type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}})
        for i in range(0, len(words), 3):
            chunk = " ".join(words[i:i + 3]) + " "
            self.out.line({"type": "stream_event", "session_id": self.session_id, "event": {
                "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": chunk}}})
            self.counts["text_deltas"] += 1
            self.usage["output_tokens"] += 3
            debt += 3 / tps
            if debt >= 0.02:
                if not self.wait(debt):
                    return False
                debt = 0.0
            elif self.interrupted.is_set():
                return False
        self.out.line({"type": "stream_event", "session_id": self.session_id,
                       "event": {"type": "content_block_stop", "index": 0}})
        return True

    def assistant(self, content: list[dict[str, Any]]) -> None:
        self.n += 1
        self.usage["input_tokens"] += 1200
        self.usage["cache_read_input_tokens"] += 9000
        self.out.line({"type": "assistant", "session_id": self.session_id, "parent_tool_use_id": None, "message": {
            "id": f"msg_stub_{self.n}", "type": "message", "role": "assistant", "model": self.model,
            "content": content, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
            "usage": {"input_tokens": 1200, "cache_read_input_tokens": 9000 + self.n * 400,
                      "cache_creation_input_tokens": 0, "output_tokens": 200}}})

    def step_text(self) -> str:
        words = [random.choice(_WORDS) for _ in range(200)]
        steers = self.take_steers()
        if steers:
            self.steers += steers
            self.counts["messages_read"] += len(steers)
            for text in steers:
                self.saw_messages(text, "stdin")
            words = ["Got your message:", *(" ".join(s.split()) for s in steers), "-", *words]
        return " ".join(words)

    def tool(self, name: str, arguments: dict[str, Any]) -> str:
        """One tool_use block, the real MCP call, and its tool_result. Returns the result text."""
        expect_s = float(arguments.pop("expect_s", 0) or 0)
        if not self.stream_text(self.step_text()):
            return ""
        self.counts["tool_calls"] += 1
        called = time.monotonic()
        if name == "ducky_ask_user":
            self.counts["ask_user"] += 1
        tool_id = f"toolu_stub_{uuid.uuid4().hex[:20]}"
        self.assistant([{"type": "text", "text": "Next step."},
                        {"type": "tool_use", "id": tool_id, "name": f"mcp__uefn__{name}", "input": arguments}])
        box: dict[str, Any] = {}

        def call() -> None:
            try:
                assert self.mcp is not None
                box["result"] = self.mcp.request("tools/call", {"name": name, "arguments": arguments})
            except Exception as exc:  # noqa: BLE001 - the error becomes the tool result
                box["error"] = str(exc)

        worker = threading.Thread(target=call, name="stub-tool", daemon=True)
        worker.start()
        while worker.is_alive() and self.wait(0.2):
            worker.join(0.0)
        result = box.get("result") or {}
        if self.interrupted.is_set() and worker.is_alive():
            content: Any = [{"type": "text", "text": "Interrupted by user"}]
            failed = True
        elif "error" in box:
            content, failed = [{"type": "text", "text": box["error"]}], True
        else:
            content = [b for b in result.get("content") or [] if isinstance(b, dict)] or [{"type": "text", "text": ""}]
            failed = bool(result.get("isError"))
        self.out.line({"type": "user", "session_id": self.session_id, "parent_tool_use_id": None, "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content,
                                         "is_error": failed}]}})
        text = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
        # seconds: how long Ducky took to answer (the harness reads terminal waits from it).
        # Terminal replies are kept short in the log, so a slow or early answer can be read.
        extra = {"reply": text[:400]} if name.startswith("ducky_terminal") else {}
        self.log("tool", tool=name, seconds=round(time.monotonic() - called, 3), failed=failed, **extra)
        if name == "ducky_ask_user":
            path = "answer"
        elif name == "ducky_terminal_run" and time.monotonic() - called < expect_s - 5:
            path = "early return + steer"
        else:
            path = "steer"
        self.saw_messages(text, path)
        return text

    # ── scenarios ──────────────────────────────────────────────────────

    def fixture_files(self) -> list[Path]:
        files = []
        for root, dirs, names in os.walk(self.repo):
            dirs[:] = [d for d in dirs if d != ".git"]
            files += [Path(root) / n for n in names]
            if len(files) > 500:
                break
        return files or [self.repo / "README.md"]

    def terminal(self, command: str, timeout_s: int, expect_s: float = 0) -> None:
        if not self.terminal_session:
            text = self.tool("ducky_terminal_open", {"shell": "powershell", "cwd": str(self.repo),
                                                     "title": "stub"})
            found = re.search(r'"session_id"\s*:\s*"([^"]+)"', text)
            self.terminal_session = found.group(1) if found else ""
        if self.terminal_session and not self.interrupted.is_set():
            args: dict[str, Any] = {"session_id": self.terminal_session, "command": command,
                                    "command_timeout_s": timeout_s}
            if expect_s:
                args["expect_s"] = expect_s  # stripped before the call; marks an early return
            self.tool("ducky_terminal_run", args)

    def gap(self) -> bool:
        low, _, high = (os.environ.get("STUB_GAP_S") or "3-8").partition("-")
        return self.wait(random.uniform(float(low), float(high or low)))

    def swarm(self, deadline: float) -> None:
        files = self.fixture_files()
        asked = False
        ask_at = time.monotonic() + (deadline - time.monotonic()) / 2
        rotation = ("plan", "read", "search", "terminal", "write")
        step = 0
        while time.monotonic() < deadline and self.gap():
            if not asked and time.monotonic() >= ask_at:
                asked = True
                self.tool("ducky_ask_user", {"questions": [{
                    "id": "stub_q", "prompt": "Stub question: anything to change before I go on?",
                    "required": True}]})
                continue
            kind = rotation[step % len(rotation)]
            step += 1
            if kind == "plan":
                self.tool("ducky_get_plan", {})
            elif kind == "read":
                self.tool("workspace_read_file", {"path": str(random.choice(files)), "end_line": 80})
            elif kind == "search":
                self.tool("workspace_search", {"pattern": "stub", "path": str(self.repo), "max_results": 20})
            elif kind == "terminal":
                self.terminal(sleep_command(terminal_s()), 120)
            else:
                self.tool("workspace_write_file", {"path": str(self.repo / "stub_out" / f"{self.session_id[:8]}-{step}.txt"),
                                                   "content": self.step_text()})

    def interrupt_scenario(self, deadline: float) -> None:
        """Phases the harness sends messages and presses Stop in (each logged as ``phase``):
        streaming text, a long terminal command, a real git commit in the fixture repo (in
        this process tree; the harness's pre-commit hook makes it slow), an ask_user wait,
        an approval-card wait. Then the turn ends."""
        seconds = int(float(os.environ.get("STUB_COMMAND_S") or 180))
        self.log("phase", phase="streaming")
        if not self.stream_text(" ".join(random.choice(_WORDS) for _ in range(1500))):
            return
        self.log("phase", phase="command")
        self.terminal(sleep_command(seconds), seconds + 120, expect_s=seconds)
        if self.interrupted.is_set():
            return
        self.log("phase", phase="git")
        note = self.repo / "stub_out" / f"commit-{self.session_id[:8]}-{int(time.time())}.txt"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("stub commit\n", encoding="utf-8")
        git = ["git", "-C", str(self.repo), "-c", "user.name=stub", "-c", "user.email=stub@example.invalid"]
        subprocess.run([*git, "add", "--", str(note)], capture_output=True, creationflags=_NO_WINDOW)
        subprocess.run([*git, "commit", "-q", "-m", "stub commit", "--", str(note)], capture_output=True,
                       creationflags=_NO_WINDOW)
        if self.interrupted.is_set() or time.monotonic() >= deadline:
            return
        self.log("phase", phase="ask")
        self.tool("ducky_ask_user", {"questions": [{"id": "stub_q", "prompt": "Stub question: go on?",
                                                    "required": True}]})
        if self.interrupted.is_set():
            return
        self.log("phase", phase="approval")
        self.tool("ducky_permission_prompt", {"tool_name": "Bash", "input": {"command": "git push"}})
        if not self.interrupted.is_set():
            self.stream_text("Committed the stub change.")

    # ── the turn ───────────────────────────────────────────────────────

    def run(self) -> int:
        started = time.monotonic()
        config = json.loads(Path(self.opts.get("mcp_config") or "").read_text(encoding="utf-8"))
        server = (config.get("mcpServers") or {}).get("uefn") or {}
        tools: list[str] = []
        status = "connected"
        try:
            self.mcp = Mcp(server)
            tools = self.mcp.start()
        except Exception:  # noqa: BLE001 - reported the way Claude reports it: status failed
            status = "failed"
        self.out.line({"type": "system", "subtype": "init", "session_id": self.session_id, "cwd": os.getcwd(),
                       "model": self.model, "permissionMode": self.opts.get("permission_mode") or "default",
                       "apiKeySource": "none", "claude_code_version": VERSION,
                       "tools": [f"mcp__uefn__{t}" for t in tools],
                       "mcp_servers": [{"name": "uefn", "status": status}]})
        self.conv = self.conv or str((server.get("env") or {}).get("DUCKY_CONV_ID") or "")
        prompt = self.first_prompt()
        self.log("turn_start", connected=status == "connected")
        self.saw_messages(prompt, "turn")
        if status == "connected":
            deadline = started + float(os.environ.get("STUB_TURN_S") or 600)
            scenario = os.environ.get("STUB_SCENARIO") or "swarm"
            (self.interrupt_scenario if scenario == "interrupt" else self.swarm)(deadline)
        if not self.interrupted.is_set():
            self.stream_text(f"Done with: {' '.join(prompt.split()[:12])}")
            self.assistant([{"type": "text", "text": "Done."}])
        if self.mcp is not None:
            self.mcp.close()
        interrupted = self.interrupted.is_set()
        self.log("turn_end", interrupted=interrupted, **self.counts)
        self.out.line({
            "type": "result", "subtype": "error_during_execution" if interrupted else
            ("success" if status == "connected" else "error_during_execution"),
            "is_error": interrupted or status != "connected", "interrupted": interrupted,
            "result": "Interrupted by user" if interrupted else "Done.", "session_id": self.session_id,
            "duration_ms": int((time.monotonic() - started) * 1000), "num_turns": self.n,
            "total_cost_usd": 0, "usage": dict(self.usage), "stub_counts": dict(self.counts),
            "modelUsage": {self.model: {"inputTokens": self.usage["input_tokens"],
                                        "outputTokens": self.usage["output_tokens"], "contextWindow": 200000}}})
        return 0 if status == "connected" else 1


def sleep_command(seconds: float) -> str:
    """A PowerShell command that sleeps, run by this stub's own Python.

    A bare ``python`` in the app's terminal can be the Microsoft Store shortcut: it exits
    at once with 9009, or hangs until the command times out."""
    exe = sys.executable.replace("'", "''")
    return f"& '{exe}' -c \"import time; time.sleep({seconds:g})\""


def terminal_s() -> float:
    """Seconds the swarm scenario's terminal command sleeps (STUB_TERMINAL_S, default 30)."""
    return float(os.environ.get("STUB_TERMINAL_S") or 30)


def hold_memory() -> bytearray | None:
    mb = int(os.environ.get("STUB_MEMORY_MB") or 0)
    if mb <= 0:
        return None
    block = bytearray(mb * 1024 * 1024)
    block[::4096] = b"\x01" * len(range(0, len(block), 4096))  # touch every page so it is really committed
    return block


def main(args: list[str]) -> int:
    if os.environ.get("STUB_SANDBOX") != "1":
        print("stub_claude refuses to run outside a sandbox (set STUB_SANDBOX=1)", file=sys.stderr)
        return 2
    if "--version" in args or args[:1] == ["-v"]:
        print(f"{VERSION} (Claude Code stub)")
        return 0
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "stub"}))
        return 0
    if args[:1] == ["auth"]:  # login / logout
        print("Stub: nothing to do.")
        return 0
    if args[:1] == ["update"]:
        print(f"Claude Code is up to date ({VERSION})")
        return 0
    opts = parse_argv(args)
    if opts.get("output_format") != "stream-json":
        prompt = " ".join(opts["positional"]) or sys.stdin.read()  # one-shot text completion
        print(f"Stub reply to: {' '.join(prompt.split()[:20])}")
        return 0
    memory = hold_memory()  # noqa: F841 - held for the whole turn
    out = Out()
    turn = Turn(opts, out)
    for name in ("SIGBREAK", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: turn.interrupted.set())
    return turn.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
