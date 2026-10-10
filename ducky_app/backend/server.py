"""Single FastMCP instance shared by all tool modules."""

from mcp.server.fastmcp import FastMCP
from contextlib import asynccontextmanager
from contextvars import ContextVar

import anyio

from backend.agent.coding_agents.plans import PLAN_PROTOCOL
from backend.agent.hard_rules import AGENT_HARD_RULES


class ProtectedFastMCP(FastMCP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # SDK lifespan runs once per connection, including dedicated stdio.
        # Preserve caller lifespan data and keep all watcher tasks inside it.
        self._catalog_watch = ContextVar("catalog_watch", default=None)
        original_lifespan = self._mcp_server.lifespan

        @asynccontextmanager
        async def lifespan(server):
            async with original_lifespan(server) as context:
                async with anyio.create_task_group() as tasks:
                    token = self._catalog_watch.set((tasks, set()))
                    try:
                        yield context
                    finally:
                        tasks.cancel_scope.cancel()
                        self._catalog_watch.reset(token)

        self._mcp_server.lifespan = lifespan
        original_options = self._mcp_server.create_initialization_options

        def initialization_options(*args, **kwargs):
            options = original_options(*args, **kwargs)
            if options.capabilities.tools is not None:
                options.capabilities.tools.listChanged = True
            return options

        self._mcp_server.create_initialization_options = initialization_options

    async def _catalog_tools(self):
        from backend.workspace.ai_ignore import current_policy, SAFE_TOOLS

        available = await super().list_tools()
        return [t for t in available if t.name in SAFE_TOOLS] if current_policy().strict else available

    async def _watch_catalog(self, session, revision):
        from backend.bridge.shared_mcp import (
            _catalog_marker, _catalog_revision, _policy_filtered, _snapshot_tools, _tool_row,
        )

        # Holding the marked objects keeps them alive, so their ids stay unique.
        marked = None
        try:
            while True:
                await anyio.sleep(0.5)
                # Rebuild and hash the rows only when a registered tool moved.
                marker = _catalog_marker(_policy_filtered(_snapshot_tools(self)))
                if marked is not None and marker[1] == marked[1]:
                    continue
                current = _catalog_revision([_tool_row(t) for t in await self._catalog_tools()])
                marked = marker
                if current != revision:
                    await session.send_tool_list_changed()
                    revision = current
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            return  # A disconnected client must not affect other sessions.

    async def list_tools(self):
        available = await self._catalog_tools()
        watch = self._catalog_watch.get()
        if watch is not None:
            from backend.bridge.shared_mcp import _catalog_revision, _tool_row

            session = self._mcp_server.request_context.session
            tasks, sessions = watch
            if session not in sessions:
                sessions.add(session)
                tasks.start_soon(self._watch_catalog, session,
                                 _catalog_revision([_tool_row(t) for t in available]))
        return available

    async def call_tool(self, name, arguments):
        from backend.agent.run_context import current_mode
        from backend.agent.toolsets.plan_safe import mode_tool_block_reason
        from mcp.server.fastmcp.exceptions import ToolError

        # This context is bound by the host, never by tool arguments or DUCKY_*
        # attribution hints. A separate process needs its own trusted binding;
        # a local ContextVar does not authenticate a remote MCP client.
        mode = current_mode()
        if mode != "agent":
            catalog = {tool.name: tool for tool in await super().list_tools()}
            reason = mode_tool_block_reason(mode, name, arguments, catalog)
            if reason:
                raise ToolError(reason)
        from backend.workspace.ai_ignore import require_safe_tool

        require_safe_tool(name)
        import asyncio
        from backend.workspace.identity import resolve_context
        from backend.agent.chat_title import require_self_name
        from backend.panel.rpc import wait_for_question_answers

        ctx = resolve_context()
        if ctx is not None and ctx.conv_id:
            await asyncio.to_thread(wait_for_question_answers, ctx.conv_id)
            effective = (arguments or {}).get("name", name) if name == "ducky_call_tool" else name
            require_self_name(effective, ctx.conv_id)
        try:
            result = await super().call_tool(name, arguments)
        except Exception as exc:
            # A desktop plugin's tool failing lands in ducky_plugin_errors (core tools are skipped).
            from backend.tools.panel.panel_plugin_check import record_tool_error

            record_tool_error(name, exc)
            raise
        from backend.agent import verify_evidence

        try:
            verify_evidence.record_result(name, arguments, result)
        except OSError:
            import logging

            logging.getLogger(__name__).exception("Could not persist verification evidence")
        return result


mcp = ProtectedFastMCP(
    "uefn-ducky",
    instructions=(
        "UEFN Ducky — MCP server for UEFN (Unreal Editor for Fortnite) plus Store desktop "
        "plugins (e.g. blender_*). Desktop-plugin tools do NOT need the UEFN listener.\n\n"
        f"{AGENT_HARD_RULES}\n"
        "**Plans (HARD):** Multi-step diagnose/fix/build (≥2 tool rounds) MUST use "
        f"`ducky_create_plan`. Never substitute a chat prose Fix plan. {PLAN_PROTOCOL}\n\n"
        "**Blender (Store plugin):** when blender tools are enabled and the Blender MCP "
        "socket is up, call blender_* immediately — never wait for UEFN / the Fortnite "
        "listener. Teach Connect only when blender_status reports disconnected.\n\n"
        "**Store plugins:** enabled plugins may register extra tools on this same MCP "
        "server (e.g. discord_*, blender_*). If those tools are listed, call them — do "
        "not claim the integration is unavailable.\n\n"
        "Performance: use compact responses (pretty=false), pagination (offset/limit/fields) on "
        "get_all_actors, list_assets, and search_assets.\n\n"
        "Workspace file tools use UEFN_VSCODE_WORKSPACE_FOLDERS. For tkinter via execute_python, "
        "use get_tk_root() and tk.Toplevel — never tk.Tk()."
    ),
)
