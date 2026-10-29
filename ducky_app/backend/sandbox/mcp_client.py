"""Talk to an MCP server a sandbox runs: start one (a stdio command inside the sandbox, such
as a test build's bridge EXE) or reach one on a port the sandbox owns, then call tools in
one session. Pictures a tool sends back are saved in the chat."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import threading
from pathlib import Path
from typing import Any

from backend.sandbox import manifest, paths
from backend.sandbox.env import sandbox_env

_TEXT_MAX = 30_000
_TOOL_LIST_MAX = 400


def _content(result: Any, conv_id: str) -> dict[str, Any]:
    texts: list[str] = []
    pictures: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            texts.append(str(getattr(block, "text", "")))
        elif kind == "image":
            try:
                from frontend.ui_web.tool_captures import save_capture_for_agents

                saved = save_capture_for_agents(base64.b64decode(getattr(block, "data", "")), prefix="sandbox_mcp",
                                                conv_id=conv_id)
                if saved.get("ok"):
                    pictures.append(str(saved.get("path")))
            except Exception:
                pass
    text = "\n".join(texts)
    parsed: Any = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
            parsed = json.loads(parsed["result"])
    except ValueError:
        parsed = None
    out: dict[str, Any] = {"is_error": bool(getattr(result, "isError", False))}
    if parsed is not None:
        out["json"] = parsed
    else:
        out["text"] = text[:_TEXT_MAX]
    if pictures:
        out["pictures"] = pictures
    return out


async def _session(params: Any, url: str, calls: list[dict[str, Any]], list_tools: bool, conv_id: str,
                   timeout_s: float) -> dict[str, Any]:
    from mcp import ClientSession

    stack = contextlib.AsyncExitStack()
    if url:
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        http = await stack.enter_async_context(create_mcp_http_client())
        transport = streamable_http_client(url, http_client=http)
    else:
        from mcp.client.stdio import stdio_client

        transport = stdio_client(params)
    async with stack, transport as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as client:
            await asyncio.wait_for(client.initialize(), timeout=timeout_s)
            out: dict[str, Any] = {"ok": True, "results": []}
            if list_tools:
                tools = (await asyncio.wait_for(client.list_tools(), timeout=timeout_s)).tools
                out["tools"] = [t.name for t in tools][:_TOOL_LIST_MAX]
                out["tool_count"] = len(tools)
            for call in calls:
                name = str(call.get("tool") or call.get("name") or "")
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                try:
                    res = await asyncio.wait_for(client.call_tool(name, args), timeout=timeout_s)
                    out["results"].append({"tool": name, **_content(res, conv_id)})
                except Exception as exc:  # noqa: BLE001 - one failed call is reported, the rest still run
                    out["results"].append({"tool": name, "is_error": True, "error": f"{type(exc).__name__}: {exc}"})
            return out


def call(conv_id: str, command: str = "", args: list[str] | None = None, url: str = "",
         calls: list[dict[str, Any]] | None = None, list_tools: bool = False, env: dict[str, str] | None = None,
         cwd: str = "", timeout_s: float = 300.0) -> dict[str, Any]:
    box = paths.sandbox_dir(conv_id, create=True)
    params = None
    if url:
        from urllib.parse import urlsplit

        from backend.sandbox.service import owned_ports

        parts = urlsplit(url)
        if parts.hostname not in ("127.0.0.1", "localhost") or (parts.port or 0) not in owned_ports(conv_id):
            return {"ok": False, "error": "url must be an MCP server on a port this chat's sandbox programs use."}
    else:
        if not command:
            return {"ok": False, "error": "Pass command (a program in the sandbox) or url."}
        from mcp import StdioServerParameters

        exe = Path(command)
        if not exe.is_absolute():
            exe = box / "work" / exe
        if not exe.exists() or not paths.is_sandbox_path(str(exe), conv_id):
            return {"ok": False, "error": f"command must be a program inside this chat's sandbox: {exe}"}
        workdir = Path(cwd) if cwd else exe.parent
        if not paths.is_sandbox_path(str(workdir), conv_id):
            return {"ok": False, "error": "cwd must be inside this chat's sandbox."}
        params = StdioServerParameters(command=str(exe), args=[str(a) for a in (args or [])],
                                       env=sandbox_env(box, env), cwd=str(workdir))
    box_calls = [c for c in (calls or []) if isinstance(c, dict)][:40]
    holder: dict[str, Any] = {}

    def worker() -> None:
        try:
            holder["out"] = asyncio.run(_session(params, url, box_calls, list_tools, conv_id, max(5.0, timeout_s)))
        except Exception as exc:  # noqa: BLE001
            holder["out"] = {"ok": False, "error": f"MCP session failed: {type(exc).__name__}: {exc}"}

    thread = threading.Thread(target=worker, name="sandbox-mcp", daemon=True)
    thread.start()
    thread.join(timeout=max(10.0, timeout_s * (len(box_calls) + 2)))
    if thread.is_alive():
        return {"ok": False, "error": "The MCP server didn't finish in time."}
    out = holder.get("out") or {"ok": False, "error": "no result"}
    manifest.record(box, "mcp", target=url or command, tools=[c.get("tool") for c in box_calls],
                    ok=out.get("ok"))
    return out
