import asyncio
from types import SimpleNamespace

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ServerNotification, ToolListChangedNotification, ToolAnnotations

from backend.server import ProtectedFastMCP
from backend.workspace import ai_ignore


def test_persistent_dedicated_catalog_notifications(monkeypatch):
    monkeypatch.setattr(ai_ignore, 'current_policy', lambda: SimpleNamespace(strict=False))
    server = ProtectedFastMCP('dedicated-catalog-test')
    calls = []
    def read(path: str = '') -> str:
        calls.append(path)
        return path
    server.add_tool(read, name='docs__read')
    async def run():
        notices = asyncio.Queue()
        async def handler(message):
            if isinstance(message, ServerNotification) and isinstance(message.root, ToolListChangedNotification):
                notices.put_nowait(message)
        async with create_connected_server_and_client_session(server, message_handler=handler) as client:
            previous = await client.list_tools()
            registry = server._tool_manager._tools
            changes = [
                lambda: server.add_tool(read, name='docs__late'),
                lambda: setattr(registry['docs__late'], 'parameters', {'type': 'object', 'required': ['path'], 'properties': {'path': {'type': 'string', 'enum': ['a', 'b']}}}),
                lambda: setattr(registry['docs__late'], 'annotations', ToolAnnotations(readOnlyHint=True, destructiveHint=False)),
                lambda: setattr(registry['docs__late'], 'meta', {'connection_state': 'unavailable', 'reason': 'disconnected'}),
                lambda: server.remove_tool('docs__read'),
                lambda: server.remove_tool('docs__late'),
            ]
            for index, change in enumerate(changes):
                change()
                with anyio.fail_after(3):
                    await notices.get()
                current = await client.list_tools()
                assert current != previous
                rows = {t.name: t for t in current.tools}
                if index == 1:
                    assert rows['docs__late'].inputSchema == registry['docs__late'].parameters
                if index == 2:
                    assert rows['docs__late'].annotations.readOnlyHint is True
                if index == 3:
                    assert rows['docs__late'].meta == {'connection_state': 'unavailable', 'reason': 'disconnected'}
                if index == 4:
                    assert set(rows) == {'docs__late'}
                if index == 5:
                    assert rows == {}
                previous = current
            assert calls == []
        await anyio.sleep(0.6)  # closed session has no orphan notifier
    asyncio.run(run())


def test_dedicated_notification_capability_and_strict_filter(monkeypatch):
    monkeypatch.setattr(ai_ignore, 'current_policy', lambda: SimpleNamespace(strict=True))
    server = ProtectedFastMCP('strict-catalog-test')
    server.add_tool(lambda: None, name='private_write')
    assert server._mcp_server.create_initialization_options().capabilities.tools.listChanged is True
    async def run():
        async with create_connected_server_and_client_session(server) as client:
            assert (await client.list_tools()).tools == []
    asyncio.run(run())


def test_dedicated_sessions_cleanup_and_policy_refresh(monkeypatch):
    from contextlib import asynccontextmanager
    policy = SimpleNamespace(strict=False)
    monkeypatch.setattr(ai_ignore, 'current_policy', lambda: policy)
    lifetimes = []
    @asynccontextmanager
    async def lifespan(server):
        lifetimes.append('open')
        try:
            yield {'custom': 'preserved'}
        finally:
            lifetimes.append('closed')
    server = ProtectedFastMCP('sessions-test', lifespan=lifespan)
    server.add_tool(lambda: None, name='private_write')
    async def run():
        queues = [asyncio.Queue(), asyncio.Queue()]
        def handler_for(queue):
            async def handler(message):
                if isinstance(message, ServerNotification) and isinstance(message.root, ToolListChangedNotification):
                    queue.put_nowait(message)
            return handler
        async with create_connected_server_and_client_session(server, message_handler=handler_for(queues[0])) as first:
            assert len((await first.list_tools()).tools) == 1
            async with create_connected_server_and_client_session(server, message_handler=handler_for(queues[1])) as second:
                assert len((await second.list_tools()).tools) == 1
                policy.strict = True
                with anyio.fail_after(3):
                    await queues[0].get()
                    await queues[1].get()
                assert (await first.list_tools()).tools == []
                assert (await second.list_tools()).tools == []
            assert lifetimes.count('closed') == 1
            policy.strict = False
            with anyio.fail_after(3):
                await queues[0].get()
            assert [t.name for t in (await first.list_tools()).tools] == ['private_write']
            assert queues[1].empty()
        assert lifetimes.count('open') == lifetimes.count('closed') == 2
        assert server._catalog_watch.get() is None
    asyncio.run(run())
