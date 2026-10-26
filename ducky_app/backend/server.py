"""Single FastMCP instance shared by all tool modules."""

from mcp.server.fastmcp import FastMCP
from contextlib import asynccontextmanager
from contextvars import ContextVar
import inspect

import anyio


def _with_steer(result, texts: list[str]):
    """Hand the person's mid-turn messages to the agent with this tool's result."""
    import json

    from mcp.types import TextContent

    note = TextContent(type="text", text=(
        "[The person just sent this while you were working. Take it into account now; it may "
        "change what you do next. Keep the turn going unless it asks you to stop.]\n" + "\n\n".join(texts)
    ))
    if hasattr(result, "content"):  # a protocol CallToolResult
        result.content = [*result.content, note]
        return result
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        return [*content, note], structured
    if isinstance(result, dict):  # structured only: keep it, add its text form
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False)), note], result
    return [*result, note]


# ponytail: one dedicated session per process, so a 2 s identity recheck is cheap; the
# shared daemon (16+ clients) keeps its 30 s net and relies on revision bumps.
DEDICATED_RECHECK_S = 2.0


def _returns_str(fn) -> bool:
    try:
        annotation = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return False
    return annotation is str or annotation == "str"


class ProtectedFastMCP(FastMCP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from backend.bridge.shared_mcp import watch_registry

        watch_registry(self)  # every tool add/remove wakes the catalog watchers
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

    def add_tool(self, fn, *args, structured_output=None, **kwargs):
        # A str tool's text is the result: FastMCP would otherwise wrap it again as
        # structuredContent {"result": "<escaped json>"} with a matching outputSchema.
        if structured_output is None and _returns_str(fn):
            structured_output = False
        return super().add_tool(fn, *args, structured_output=structured_output, **kwargs)

    async def _catalog_tools(self):
        from backend.workspace.ai_ignore import current_policy, SAFE_TOOLS

        available = await super().list_tools()
        if current_policy().strict:
            return [t for t in available if t.name in SAFE_TOOLS]
        try:
            from backend.uefn_plugins.host import sensitive_tool_block_reason

            # A client whose run names its model never sees sensitive tools it may not call.
            return [t for t in available if not sensitive_tool_block_reason(t.name, listing=True)]
        except Exception:
            return available

    async def _watch_catalog(self, session, revision):
        from backend.bridge.shared_mcp import (
            _catalog_marker, _catalog_revision, _policy_filtered, _snapshot_tools, _tool_row,
            catalog_revision, wait_catalog_change,
        )

        # Holding the marked objects keeps them alive, so their ids stay unique.
        marked = None
        seen = catalog_revision()
        try:
            while True:
                # Sleep until a tool is added or removed, or a short recheck: an in-place
                # edit or a policy flip in this process bumps nothing, and the identity
                # compare below costs a few ms (no rows rebuilt unless something moved).
                seen = await anyio.to_thread.run_sync(
                    wait_catalog_change, seen, None, DEDICATED_RECHECK_S, abandon_on_cancel=True)
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
        from backend.agent.toolsets.plan_safe import mode_permission_answer, mode_tool_block_reason
        from mcp.server.fastmcp.exceptions import ToolError

        # This context is bound by the host, never by tool arguments or DUCKY_*
        # attribution hints. A separate process needs its own trusted binding;
        # a local ContextVar does not authenticate a remote MCP client.
        mode = current_mode()
        if mode != "agent":
            catalog = {tool.name: tool for tool in await super().list_tools()}
            answer = mode_permission_answer(mode, name, arguments, catalog)
            if answer is not None:
                from mcp.types import TextContent

                return [TextContent(type="text", text=answer)]
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
        if ctx is not None and ctx.conv_id:
            from backend.panel.rpc import ack_steer, take_steer_messages

            lease_id, texts = await asyncio.to_thread(take_steer_messages, ctx.conv_id)
            if texts:
                result = _with_steer(result, texts)
                # Only now is the message in what the agent gets. A failed ack is harmless:
                # the lease runs out and the next tool call carries the message.
                await asyncio.to_thread(ack_steer, ctx.conv_id, lease_id)
        return result


mcp = ProtectedFastMCP(
    "uefn-ducky",
    # The agent rules and plan protocol travel in the coding-agent prompt; repeating
    # them here sent them twice every session.
    instructions=(
        "UEFN Ducky — tools for UEFN (Unreal Editor for Fortnite), project files and the Ducky app.\n"
        "Store-plugin tools (blender_*, discord_*, …) and nested servers ({prefix}__*) are not listed:\n"
        "find them with ducky_get_tools and run them with ducky_call_tool(name, arguments).\n"
        "Desktop-plugin tools do not need the UEFN listener.\n"
        "Keep responses compact (pretty=false) and page big lists (offset/limit/fields).\n"
        "Not sure which tool fits? skill_search, or ducky_get_tools."
    ),
)
