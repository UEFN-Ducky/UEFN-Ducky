"""Ten everyday coding-agent tasks against the real workspace tools on this repo.

Measures what an agent pays per call: bytes and tokens (chars / 4) on the wire (the whole
tools/call result, content and structuredContent), seconds, and hits next to what
``git grep`` / ``git ls-files`` say is really there. Slow on purpose (it searches the
whole repo), so it runs only with ``DUCKY_TOOL_BENCH=1``, from the repo root:

    DUCKY_TOOL_BENCH=1 py -3 -m pytest ducky_app/backend/tools/core/test_tool_bench.py -s

``DUCKY_TOOL_BENCH_OUT=<file.json>`` also saves the rows, to compare two runs.
``test_prompt_sizes`` measures the coding-agent prompt and the server instructions.
``test_tool_surface`` (also ``DUCKY_TOOL_BENCH_LIVE``) starts the stdio bridge the way a
coding agent does and counts the tools it lists, per prefix and source. Under pytest the
app data is isolated, so for the real list (plugins, nested servers) set it to a running
chat's MCP config: ``%LOCALAPPDATA%/UEFN-Ducky/coding_agents/tmp/uefn-mcp-<chat>.*.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from backend.tools.core import system, workspace_code as wc
from backend.workspace import runtime
from backend.workspace.writer import ProjectWriter

pytestmark = pytest.mark.skipif(
    not os.environ.get("DUCKY_TOOL_BENCH"), reason="tool benchmark: set DUCKY_TOOL_BENCH=1"
)

REPO = Path(__file__).resolve().parents[4]
PANEL_UI = "ducky_app/backend/tools/panel/panel_ui.py"
READ_FILES = [
    PANEL_UI,
    "ducky_app/backend/tools/core/workspace_code.py",
    "ducky_app/backend/util/json_util.py",
]
TS_GLOB = "*.ts,*.tsx"


class _Journal:
    def record(self, record):
        return {"run_id": "bench", "seq": 1}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        pass


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def _grep_truth(pattern: str, globs: str = "") -> int:
    """Lines git grep finds (case-insensitive, fixed string) in tracked and untracked,
    not-ignored files: what a search of the project should see."""
    specs = [g.strip() for g in globs.split(",") if g.strip()]
    out = _git("grep", "--untracked", "-c", "-i", "-F", "-e", pattern, "--", *specs)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if ":" in line)


def _wire(name: str, args: dict[str, Any], fn: Callable[..., Any]) -> tuple[str, dict[str, Any], str]:
    """The tools/call result a client receives, through the real server's handler.

    Falls back to calling the function and wrapping it the way FastMCP wraps a str tool."""
    try:
        from mcp.types import CallToolRequest, CallToolRequestParams

        from backend.server import mcp

        handler = mcp._mcp_server.request_handlers[CallToolRequest]
        res = asyncio.run(handler(CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name=name, arguments=args))))
        dump = res.root.model_dump(by_alias=True, exclude_none=True)
        text = "".join(c.get("text", "") for c in dump.get("content", []))
        if dump.get("isError"):
            raise RuntimeError(text)
        return text, dump, "server"
    except Exception:  # noqa: BLE001 - measure the function itself instead
        out = fn(**args)
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        dump = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(out, str):
            dump["structuredContent"] = {"result": out}
        return text, dump, "direct"


def _search_hits(text: str, pattern: str) -> int:
    """Reported hits: total_hits when the result says it, else matching lines shown."""
    m = re.search(r'"?total_hits"?\s*[:=]\s*(\d+)', text)
    if m:
        return int(m.group(1))
    return sum(1 for line in text.splitlines() if pattern.lower() in line.lower())


def _truncated(name: str, text: str) -> bool:
    """Only listings can be cut short; a read's file text may say "truncated" itself."""
    if name not in ("workspace_search", "workspace_find", "workspace_git"):
        return False
    return bool(re.search(r"truncated|INCOMPLETE|Stopped after|Stopped at", text, re.IGNORECASE))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    """The open project is this repo; ``use_root`` points it elsewhere (the write task)."""
    state = {"root": REPO}

    def resolve(rel: str) -> str:
        if rel and os.path.isabs(rel):
            return rel
        rel = (rel or ".").replace("\\", "/").strip("/")
        return str(state["root"] if rel in ("", ".") else state["root"] / rel)

    def use_root(root: Path) -> None:
        state["root"] = root
        runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=_Journal()))

    use_root(REPO)
    monkeypatch.setattr(wc, "resolve_workspace_path", resolve)
    monkeypatch.setattr(system, "resolve_workspace_path", resolve)
    monkeypatch.setattr("frontend.ui_web.verse_editor.agent_sync.emit_for_bridge_tool", lambda *a, **k: None)
    try:
        from backend.server import mcp
        from backend.workspace import identity

        monkeypatch.setattr(mcp, "_ducky_skip_plugin_wait", True, raising=False)
        monkeypatch.setattr(identity, "resolve_context", lambda: None)
    except Exception:  # noqa: BLE001
        pass
    yield tmp_path, use_root
    runtime.reset_for_tests(None)


def _tasks(tmp: Path) -> list[tuple[str, str, dict[str, Any], Callable[..., Any], Callable[[str], Any], Any]]:
    """(label, tool name, args, function, hits-from-result, expected hits)."""
    panel_300 = "".join((REPO / PANEL_UI).read_text(encoding="utf-8").splitlines(keepends=True)[:300])
    verse_truth = len(_git("ls-files", "--", "*.verse").split())
    rows = []
    for pattern, glob in (("settings_changed", ""), ("voice_enabled", ""), ("def tool_json", ""),
                          ("RichChart", TS_GLOB), ("speakReply", TS_GLOB)):
        args = {"pattern": pattern, **({"glob": glob} if glob else {})}
        rows.append((f"search {pattern}", "workspace_search", args, wc.workspace_search,
                     lambda t, p=pattern: _search_hits(t, p), _grep_truth(pattern, glob)))
    rows.append(("read panel_ui.py 1-300", "workspace_read_file",
                 {"path": PANEL_UI, "start_line": 1, "end_line": 300}, system.workspace_read_file,
                 lambda t: 300 if "def " in t else 0, 300))
    rows.append(("read_files x3", "workspace_read_files", {"paths": READ_FILES}, wc.workspace_read_files,
                 lambda t: sum(1 for p in READ_FILES if p.rsplit("/", 1)[1] in t), 3))
    target = tmp / "bench_write.txt"
    target.write_text(panel_300, encoding="utf-8")
    rows.append(("write temp file (300-line overwrite)", "workspace_write_file",
                 {"path": str(target), "content": panel_300 + "# one more line\n"}, system.workspace_write_file,
                 lambda t: 1 if target.read_text(encoding="utf-8").endswith("# one more line\n") else 0, 1))
    # ponytail: a fixed commit, so every run diffs the same text (the baseline's HEAD~1..HEAD).
    span = ["ac726856~1", "ac726856"]
    rows.append(("git diff ac726856~1..ac726856", "workspace_git", {"command": "diff", "args": span}, wc.workspace_git,
                 lambda t: t.count("diff --git"), _git("diff", *span).count("diff --git")))
    rows.append(("find *.verse", "workspace_find", {"pattern": "*.verse"}, wc.workspace_find,
                 lambda t: len(set(re.findall(r"[\w./\\-]+\.verse", t))), verse_truth))
    return rows


def test_tool_bench(repo, capsys) -> None:
    tmp, use_root = repo
    results: list[dict[str, Any]] = []
    for label, name, args, fn, hits_of, expected in _tasks(tmp):
        if name == "workspace_write_file":
            # A temp folder is the project for the write, so nothing lands in the repo.
            use_root(tmp)
            args = {**args, "path": Path(args["path"]).name}
        t0 = time.perf_counter()
        try:
            text, dump, via = _wire(name, args, fn)
            error = ""
        except Exception as exc:  # noqa: BLE001 - a failing task is a result too
            text, dump, via, error = "", {}, "error", f"{type(exc).__name__}: {exc}"[:200]
        seconds = time.perf_counter() - t0
        wire = json.dumps(dump, ensure_ascii=False)
        results.append({
            "task": label, "tool": name, "via": via, "seconds": round(seconds, 2),
            "text_bytes": len(text.encode("utf-8")), "wire_bytes": len(wire.encode("utf-8")),
            "wire_tokens": len(wire) // 4, "hits": hits_of(text) if text else 0, "expected": expected,
            "truncated": _truncated(name, text), "error": error,
        })
        if name == "workspace_write_file":
            use_root(REPO)

    lines = ["| task | s | wire B | tokens | text B | hits / expected | truncated | via |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['task']} | {r['seconds']} | {r['wire_bytes']} | {r['wire_tokens']} | {r['text_bytes']} "
                     f"| {r['hits']} / {r['expected']} | {'yes' if r['truncated'] else ''} | {r['via']}"
                     f"{' ' + r['error'] if r['error'] else ''} |")
    total_s = sum(r["seconds"] for r in results)
    total_tok = sum(r["wire_tokens"] for r in results)
    right = sum(1 for r in results if r["hits"] == r["expected"] and not r["error"])
    lines.append(f"| **total** | {round(total_s, 1)} | {sum(r['wire_bytes'] for r in results)} | {total_tok} | "
                 f"{sum(r['text_bytes'] for r in results)} | {right}/{len(results)} right | | |")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    out = os.environ.get("DUCKY_TOOL_BENCH_OUT")
    if out:
        Path(out).write_text(json.dumps({"rows": results, "table": lines}, indent=2), encoding="utf-8")
    assert len(results) == 10
    assert not any(r["via"] == "error" for r in results), [r["error"] for r in results if r["error"]]


def _sentences(text: str) -> list[str]:
    """Rule-sized pieces (lines, split at sentence ends) long enough to count as a repeat."""
    parts = re.split(r"\n+|(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 40]


def test_prompt_sizes(capsys) -> None:
    """What a coding agent is sent before it does anything, and how much of it is said twice."""
    from backend.agent.coding_agents.mcp_inject import bootstrap_system_prompt
    from backend.agent.hard_rules import AGENT_HARD_RULES
    from backend.agent.prompt import CHAT_REPORT_RULE
    from backend.server import mcp

    try:
        from backend.agent.coding_agents.mcp_inject import deployed_skill_packs

        _, packs = deployed_skill_packs("claude")
    except Exception:  # noqa: BLE001 - no deployed packs on this PC
        packs = []
    prompt = bootstrap_system_prompt(
        project_root=str(REPO), listener_online=False, conv_id="00000000-0000-0000-0000-000000000000",
        ducky_name="Bench", ducky_personality="Senior gameplay programmer.", skill_names=packs,
    )
    server = mcp.instructions or ""
    server_set = set(_sentences(server))
    prompt_parts = _sentences(prompt)
    in_both = sum(len(p) for p in prompt_parts if p in server_set)
    seen: set[str] = set()
    twice_in_prompt = 0
    for p in prompt_parts:
        if p in seen:
            twice_in_prompt += len(p)
        seen.add(p)
    rows = [
        ("coding-agent prompt (bootstrap_system_prompt)", len(prompt)),
        (f"  of which skill pack list ({len(packs)} packs)", len(", ".join(packs))),
        ("server instructions (backend.server.mcp)", len(server)),
        ("AGENT_HARD_RULES", len(AGENT_HARD_RULES)),
        ("CHAT_REPORT_RULE", len(CHAT_REPORT_RULE)),
        ("prompt + instructions per session", len(prompt) + len(server)),
        ("repeated: prompt text also in instructions", in_both),
        ("repeated: said twice inside the prompt", twice_in_prompt),
    ]
    lines = ["| text | chars | tokens |", "|---|---|---|"]
    lines += [f"| {label} | {n} | {n // 4} |" for label, n in rows]
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    out = os.environ.get("DUCKY_TOOL_BENCH_OUT")
    if out:
        path = Path(out).with_name(Path(out).stem + "_prompt.json")
        path.write_text(json.dumps({label.strip(): n for label, n in rows}, indent=2), encoding="utf-8")
    assert prompt and server


def _source(name: str, core: set[str]) -> str:
    if "__" in name:
        return "nested:" + name.split("__", 1)[0]
    return "core" if name in core else "plugin"


@pytest.mark.skipif(not os.environ.get("DUCKY_TOOL_BENCH_LIVE"), reason="needs the running Ducky app")
def test_tool_surface(capsys) -> None:
    """The tool list a coding agent gets from the real stdio bridge, grouped by prefix and source."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import backend.tools  # noqa: F401 - registers the core tools in-process
    from backend.agent.coding_agents.mcp_inject import build_uefn_mcp_servers
    from backend.server import mcp

    live = os.environ["DUCKY_TOOL_BENCH_LIVE"]
    if os.path.isfile(live):
        block = json.loads(Path(live).read_text(encoding="utf-8"))["mcpServers"]["uefn"]
    else:
        block = build_uefn_mcp_servers()["uefn"]
    params = StdioServerParameters(
        command=block["command"], args=list(block.get("args") or []),
        env={**os.environ, **(block.get("env") or {})},
    )

    async def listed():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    tools = asyncio.run(asyncio.wait_for(listed(), 180))
    core = {t.name for t in asyncio.run(mcp.list_tools())}
    by_source: dict[str, int] = {}
    by_prefix: dict[str, int] = {}
    aliases = []
    schema_chars = 0
    source_chars: dict[str, int] = {}
    for t in tools:
        src = _source(t.name, core)
        by_source[src] = by_source.get(src, 0) + 1
        size = len(json.dumps(t.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False))
        source_chars[src] = source_chars.get(src, 0) + size
        schema_chars += size
        prefix = t.name.split("__", 1)[0] + "__" if "__" in t.name else t.name.split("_", 1)[0] + "_"
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        desc = (t.description or "").strip()
        # The alias itself says so; a canonical tool merely naming its alias ("Alias: x") is not one.
        if re.match(r"(?i)alias\b", desc) or re.search(r"(?i)\balias (of|for)\b", desc):
            aliases.append(t.name)
    lines = [f"tools listed: {len(tools)} (tools/list ~{schema_chars // 4} tokens)",
             "by source: " + ", ".join(f"{k} {v} (~{source_chars[k] // 4} tok)"
                                       for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])),
             "by prefix: " + ", ".join(f"{k} {v}" for k, v in sorted(by_prefix.items(), key=lambda kv: -kv[1])),
             f"aliases ({len(aliases)}): " + ", ".join(sorted(aliases))]
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    out = os.environ.get("DUCKY_TOOL_BENCH_OUT")
    if out:
        path = Path(out).with_name(Path(out).stem + "_surface.json")
        path.write_text(json.dumps({"total": len(tools), "schema_chars": schema_chars, "by_source": by_source,
                                    "by_prefix": by_prefix, "aliases": sorted(aliases)}, indent=2), encoding="utf-8")
    assert tools
