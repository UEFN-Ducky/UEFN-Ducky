"""Perf-trace rows for big plugin panel RPC replies (``plugin_call``)."""

from __future__ import annotations

PLUGIN_REPLY_BIG_BYTES = 64 * 1024


def plugin_rpc_source(plugin_id: str, method: str) -> str:
    """The perf-trace name of one plugin panel RPC: ``plugin_call:<plugin>.<method>``."""
    return f"plugin_call:{plugin_id or '?'}.{method or '?'}"


def trace_big_plugin_reply(plugin_id: str, method: str, encoded: str) -> int:
    """The bridge only sees ``plugin_call``; name the plugin RPC behind a big reply
    as a ``ui_js_big`` row so ``ui_js_big_top`` ranks it per plugin and method."""
    nbytes = len(encoded)
    if nbytes > PLUGIN_REPLY_BIG_BYTES:
        try:
            from frontend.perf_trace import trace

            source = plugin_rpc_source(plugin_id, method)
            trace("ui_js_big", source, 0.0, source=source, js_bytes=nbytes)
        except Exception:  # noqa: BLE001
            pass
    return nbytes
