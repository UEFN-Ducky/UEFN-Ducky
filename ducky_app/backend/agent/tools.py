"""MCP tool schema conversion and execution with structured errors."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.types import Tool


def _ensure_mcp():
    """Lazy: FastMCP + tool registration are ~800ms+ — not needed until a turn runs."""
    from backend.server import mcp

    import backend.tools  # noqa: F401 — register tools

    return mcp


@dataclass
class ToolCallResult:
    ok: bool
    tool: str
    data: str = ""
    error: str = ""
    hint: str = ""
    duration_ms: int = 0

    def to_json_str(self) -> str:
        payload: dict[str, Any] = {"ok": self.ok, "tool": self.tool}
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
            if self.hint:
                payload["hint"] = self.hint
        return json.dumps(payload, ensure_ascii=False)


def _with_plan_tick_nudge(name: str, result: ToolCallResult) -> ToolCallResult:
    if not result.ok or not result.data:
        return result
    try:
        from backend.agent.coding_agents.plans import format_plan_tick_nudge_for_tool

        extra = format_plan_tick_nudge_for_tool(name)
    except Exception:
        extra = ""
    if extra and extra not in result.data:
        result.data = (result.data + "\n" + extra)[:12000]
    return result


@dataclass
class ToolCallRecord:
    id: str
    name: str
    arguments: dict[str, Any]
    started: float = 0.0
    duration_ms: int = 0
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | success | error | rejected | cancelled
    llm_content: str = ""  # the envelope actually sent to the model
    llm_tokens: int = 0


from backend.agent.mcp_content import mcp_content_to_text as _content_to_text


def _hint_for_error(tool: str, text: str) -> str:
    low = text.lower()
    if "verse_compile_required" in low or ("stop" in low and "wiring blocked" in low):
        return (
            "STOP is advisory. list_verse_property_hashes(refresh=true), "
            "re-inspect, then wire_verse_* once. Do not ask the user to Build Verse."
        )
    if tool.startswith("blender_"):
        return "Open Blender (addon auto-starts). Restart Blender once after first plugin install."
    if "listener" in low or "connection" in low:
        return _LISTENER_OFFLINE_HINT
    if tool.startswith("wire_verse"):
        return (
            "Check get_verse_editables mangled_name / resolution_tried, then wire. "
            "STOP is advisory — do not abort or ask the user to Build Verse."
        )
    return ""


async def list_mcp_tools() -> list[Tool]:
    from backend.agent.builtin_toolsets import filter_builtin_tools
    from backend.uefn_plugins.host import filter_uefn_plugin_tools

    mcp = _ensure_mcp()
    core = filter_uefn_plugin_tools(filter_builtin_tools(await mcp.list_tools()))
    try:
        from backend.mcp_plugins.client_pool import get_plugin_pool

        plugin_tools = await get_plugin_pool().list_all_plugin_tools()
    except Exception:
        plugin_tools = []
    return list(core) + plugin_tools


# Params hidden from the LLM; the server-side default already does the right thing.
_HIDDEN_PARAMS = frozenset({"pretty"})

_TRIVIAL_DEFAULTS = ("", None, False, 0, 0.0, [], {})


def _slim_schema(node: Any) -> Any:
    """Strip FastMCP schema noise: titles, anyOf-null wrappers, trivial defaults."""
    if isinstance(node, list):
        return [_slim_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key == "default" and any(value is d or value == d for d in _TRIVIAL_DEFAULTS):
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _slim_schema(v) for k, v in value.items() if k not in _HIDDEN_PARAMS}
            continue
        out[key] = _slim_schema(value)
    # anyOf: [X, null] → X (optionality is conveyed by absence from `required`)
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        non_null = [x for x in any_of if x != {"type": "null"}]
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            merged = dict(non_null[0])
            for k, v in out.items():
                if k != "anyOf" and k not in merged:
                    merged[k] = v
            return merged
    return out


def _slim_description(text: str) -> str:
    """Dedent docstring bodies so indentation doesn't cost tokens."""
    lines = (text or "").splitlines()
    return "\n".join(ln.strip() for ln in lines).strip()


def _slim_tool_schema(tool: Tool) -> dict[str, Any]:
    schema = _slim_schema(dict(tool.inputSchema or {"type": "object", "properties": {}}))
    required = schema.get("required")
    if isinstance(required, list):
        kept = [r for r in required if r not in _HIDDEN_PARAMS]
        if kept:
            schema["required"] = kept
        else:
            schema.pop("required", None)
    return schema


def mcp_tool_to_anthropic(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": _slim_description(tool.description or "") or tool.name,
        "input_schema": _slim_tool_schema(tool),
    }


def mcp_tool_to_openai(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": _slim_description(tool.description or "") or tool.name,
            "parameters": _slim_tool_schema(tool),
        },
    }


def mcp_tool_to_gemini(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": _slim_description(tool.description or "") or tool.name,
        "parameters": _slim_tool_schema(tool),
    }


MARKDOWN_TOOLS = frozenset({"uefn_skill"})
API_TOOL_RESULT_MAX = 2200
# Tools whose payload IS the deliverable get a bigger budget: capping the error
# list to a stub left the model editing blind (it "fixed errors" it never saw).
TOOL_RESULT_MAX_OVERRIDES = {"workspace_list_verse_errors": 9000}

# User-paced panel tools (modal / walkthrough) need longer than the default 180s.
# ducky_ask_user NEVER times out — the agent suspends until the user answers;
# Stop (cancel_event) is the only way out. A 300s cap made the agent "proceed
# anyway" and orphan the questionnaire so the eventual answer went nowhere.
TOOL_TIMEOUT_OVERRIDES: dict[str, float] = {
    "ducky_walkthrough_run": 300.0,
    "ducky_ask_user": float("inf"),
}


def tool_result_max(tool_name: str) -> int:
    return TOOL_RESULT_MAX_OVERRIDES.get(tool_name, API_TOOL_RESULT_MAX)


def tool_timeout(tool_name: str) -> float:
    return TOOL_TIMEOUT_OVERRIDES.get(tool_name, 180.0)
# Keep in sync with serialization._TRUNC_MARK — must not tell the model to retry.
TRUNC_MARK = (
    "…[omitted: result too large for context. Do NOT repeat this call with the same "
    "arguments — it returns the same thing. Narrow the query, request specific "
    "fields/paths, or continue with what you already have.]"
)


_BLOB_KEYS = frozenset(
    {"base64", "data_base64", "image_base64", "png_base64", "audio_base64"}
)


def _strip_oversized_blobs(value: Any) -> Any:
    """Drop huge base64 blobs from tool JSON before LLM / store formatting.

    Coding agents that talk to MCP directly still need the tool itself to stop
    emitting blobs; this is defense-in-depth for the embedded ducky path.
    """
    if isinstance(value, list):
        return [_strip_oversized_blobs(v) for v in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        if (
            isinstance(key, str)
            and key.lower() in _BLOB_KEYS
            and isinstance(item, str)
            and len(item) > 200
        ):
            out[key] = f"[omitted {len(item)} chars — use path/media_url]"
            continue
        out[key] = _strip_oversized_blobs(item)
    return out


def compact_json_value(tool_name: str, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    value = _strip_oversized_blobs(value)
    if tool_name == "ping":
        # A health check must never blow the token budget. The listener's full
        # `commands` array (~100 entries) + Python banner is noise to the model;
        # when it's present, keep only liveness fields + a count. Without this,
        # ping overflowed the 2200-char cap, got nuked to the truncation stub,
        # and the model looped re-calling it. Small ping payloads (e.g. a bare
        # {"online": true}) are left untouched.
        cmds = value.get("commands")
        if isinstance(cmds, list) and len(cmds) > 12:
            slim = {k: value[k] for k in ("status", "version", "port", "listener_capabilities") if k in value}
            slim["commands_count"] = len(cmds)
            return slim
        return value
    if tool_name == "inspect_creative_device":
        settings = value.get("settings")
        # Keyed reads are already small — never strip MaxPlayers etc.
        if value.get("keys_filtered"):
            return value
        if isinstance(settings, dict) and len(settings) > 8:
            return {
                "actor_path": value.get("actor_path"),
                "label": value.get("label"),
                "class": value.get("class"),
                "kind": value.get("kind"),
                "description": value.get("description"),
                "settings_count": len(settings),
                "settings_note": (
                    'Full settings omitted — re-call with keys=["MaxPlayers", '
                    '"Matchmaking_MaxPlayersPerSession", …] (or other ToyOptions keys).'
                ),
            }
    if tool_name == "list_verse_reference_types":
        refs = value.get("reference_types")
        if isinstance(refs, list) and len(refs) > 4:
            return {
                "reference_types": refs[:4],
                "reference_types_truncated": len(refs) - 4,
                "patterns": value.get("patterns"),
                "verify": value.get("verify"),
            }
    if tool_name == "get_all_actors":
        actors = value.get("actors")
        if isinstance(actors, list) and len(actors) > 12:
            return {
                "actors": actors[:12],
                "actors_truncated": len(actors) - 12,
                "count": value.get("count", len(actors)),
            }
    if tool_name == "find_devices":
        devices = value.get("devices")
        if isinstance(devices, list) and len(devices) > 8:
            return {
                "devices": devices[:8],
                "devices_truncated": len(devices) - 8,
                "count": value.get("count", len(devices)),
            }
    if tool_name in ("workspace_write_file", "create_project_verse_file"):
        slim = dict(value)
        slim.pop("before_content", None)
        slim.pop("path", None)
        return slim
    if tool_name == "workspace_list_verse_errors":
        files = value.get("files")
        if isinstance(files, list):
            errors = sum(int(f.get("errors") or 0) for f in files if isinstance(f, dict))
            warnings = sum(int(f.get("warnings") or 0) for f in files if isinstance(f, dict))
            if errors == 0 and warnings == 0:
                return {
                    "files_scanned": len(files),
                    "errors": 0,
                    "warnings": 0,
                    "stale_count": value.get("stale_count", 0),
                    "from_cache": value.get("from_cache"),
                }
            # Flatten to one row per problem so the model always sees the actual
            # errors (file, line, message). Per-file dicts with nested `items`
            # lists blew the result cap and degraded to an opaque stub — the
            # model then "fixed" errors it never read. Errors sort before
            # warnings so a row cap never hides an error behind warnings.
            rows: list[dict[str, Any]] = []
            for f in files:
                if not isinstance(f, dict):
                    continue
                path = str(f.get("path") or "")
                for it in f.get("items") or []:
                    if not isinstance(it, dict):
                        continue
                    msg = str(it.get("message") or "")
                    rows.append(
                        {
                            "file": path,
                            "line": int(it.get("line") or 0),
                            "severity": str(it.get("severity") or "error"),
                            "message": msg if len(msg) <= 160 else msg[:160] + "…",
                        }
                    )
            rows.sort(key=lambda r: r["severity"] != "error")
            out = {
                "errors": errors,
                "warnings": warnings,
                "files_with_problems": sum(
                    1 for f in files if isinstance(f, dict) and (f.get("errors") or f.get("warnings"))
                ),
                "problems": rows[:40],
            }
            if len(rows) > 40:
                out["problems_truncated"] = len(rows) - 40
                out["hint"] = "Fix the listed problems first, then re-run to see the rest."
            return out
    return value


def shrink_str_field(obj: dict[str, Any], key: str, overflow: int) -> bool:
    """Cut `overflow` chars from obj[key] (string), keeping valid JSON. True if shrunk."""
    val = obj.get(key)
    if not isinstance(val, str) or len(val) <= overflow + len(TRUNC_MARK):
        return False
    obj[key] = val[: len(val) - overflow - len(TRUNC_MARK)] + TRUNC_MARK
    return True


def shrink_structured_value(value: Any, keep: int = 6, _depth: int = 0) -> Any:
    """Shrink an oversized structured tool result so it fits the API budget.

    Unlike shrink_str_field (which char-slices a string) this degrades dict/list
    payloads gracefully and RECURSIVELY: lists are cut to the first `keep`
    entries with a trailing marker (dict fields that are large lists also get a
    `<field>_truncated` count), long string fields are clipped, and nested
    dicts/lists are shrunk in place — a short outer list wrapping huge inner
    lists must still shrink, not fall through to the opaque stub. Scalar
    sibling fields (status/version/counts) survive — the model keeps a usable
    result instead of an opaque stub it can only retry.
    """
    if _depth > 4:
        return value
    if isinstance(value, list):
        head = [shrink_structured_value(v, keep, _depth + 1) for v in value[:keep]]
        if len(value) <= keep:
            return head
        return head + [f"__+{len(value) - keep} more (truncated)__"]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, list) and len(v) > keep:
                out[k] = [shrink_structured_value(x, keep, _depth + 1) for x in v[:keep]]
                out[f"{k}_truncated"] = len(v) - keep
            elif isinstance(v, (list, dict)):
                out[k] = shrink_structured_value(v, keep, _depth + 1)
            elif isinstance(v, str) and len(v) > 800:
                out[k] = v[:800] + TRUNC_MARK
            else:
                out[k] = v
        return out
    return value


def compact_tool_result_for_api(tool_name: str, payload_json: str) -> str:
    """Shrink tool results as compact JSON (legacy / json format path)."""
    from backend.agent.serialization import prepare_tool_result_envelope

    prepared = prepare_tool_result_envelope(tool_name, payload_json)
    if isinstance(prepared, str):
        return prepared
    obj = prepared
    data = obj.get("data")
    if isinstance(data, (dict, list)):
        obj = dict(obj)
        obj["data"] = json.dumps(data, ensure_ascii=False)
    limit = tool_result_max(tool_name)
    out = json.dumps(obj, ensure_ascii=False)
    for key in ("data", "error"):
        if len(out) <= limit:
            return out
        if shrink_str_field(obj, key, len(out) - limit):
            out = json.dumps(obj, ensure_ascii=False)
    if len(out) <= limit:
        return out
    stub: dict[str, Any]
    if obj.get("ok"):
        stub = {"ok": True, "tool": obj.get("tool", tool_name), "data": TRUNC_MARK}
    else:
        stub = {"ok": obj.get("ok"), "tool": obj.get("tool", tool_name), "error": "result too large"}
    return json.dumps(stub, ensure_ascii=False)

# Catalog / docs: tools that never talk to the live UEFN editor listener.
# execute_tool does NOT pre-block on listener health — each tool hits the listener
# only if its implementation calls send_command / api.listener().
HOST_ONLY_TOOLS = frozenset(
    {
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_write_file",
        "workspace_list_verse_errors",
        "workspace_open_verse_file",
        "workspace_compile_verse",
        "workspace_push_verse_changes",
        "code_list_errors",
        "code_detect_project",
        "code_open_file",
        "project_memory_list",
        "project_memory_get",
        "project_memory_save",
        "project_memory_append",
        "project_memory_delete",
        "ducky_memory_overview",
        "uefn_skill",
        "uefn_editor_python_hints",
        "skill_read_subskill",
        "list_verse_digests",
        "list_verse_types",
        "list_verse_devices",
        "list_verse_modules",
        "search_verse_digest",
        "get_verse_api",
        "ducky_get_status",
        "ducky_get_local_project",
        "ducky_list_projects",
        "ducky_set_project",
        "ducky_get_errors",
        "ducky_list_chats",
        "ducky_read_chat",
        "ducky_create_chat",
        "ducky_create_folder",
        "ducky_group_create",
        "ducky_group_invite",
        "ducky_group_members",
        "ducky_group_set_leader",
        "ducky_group_add_member",
        "ducky_rename_chat",
        "ducky_move_chat",
        "ducky_send_chat_message",
        "ducky_list_duckies",
        "ducky_spawn_chat",
        "ducky_recycle_member",
        "ducky_recycle_subagent",
        "ducky_agent_list",
        "ducky_agent_send",
        "ducky_agent_inbox",
        "ducky_agent_transcript",
        "ducky_agent_stop",
        "ducky_append_chat_message",
        "ducky_get_chat_context",
        "ducky_reset_chat_context",
        "ducky_restore_chat_context",
        "ducky_terminal_open",
        "ducky_terminal_run",
        "ducky_terminal_read_output",
        "ducky_terminal_list",
        "ducky_terminal_close",
    }
)


def is_host_only_tool(name: str) -> bool:
    """True when this tool does not use the UEFN editor listener.

    Panel / Verse files / digests / Blender talk to disk or their own backends.
    Editor tools (spawn_actor, wire_*, materials, …) use the listener inside the tool.
    """
    if name in HOST_ONLY_TOOLS:
        return True
    return name.startswith(("ducky_", "workspace_", "project_memory_", "blender_"))


def _requires_uefn_listener(name: str) -> bool:
    """Opt-in: only tools that talk to the live UEFN editor HTTP listener.

    Host tools (Verse writes, digests, ducky_*, blender_*), nested MCP
    (`prefix__tool`), and desktop plugin tools marked ``api.tool(listener=False)``
    (e.g. Duck-Tac-Toe cache) never hit this gate. Editor desktop plugins
    (materials, spawn_*, …) still require the listener.
    """
    if is_host_only_tool(name):
        return False
    # Duck-Tac-Toe is host-cache-only; keep working even before plugin zip bump.
    if name.startswith("ducktactoe_"):
        return False
    try:
        from backend.mcp_plugins.registry import is_plugin_tool

        if is_plugin_tool(name):
            return False
    except Exception:
        pass
    try:
        from backend.uefn_plugins.host import is_plugin_host_only_tool

        if is_plugin_host_only_tool(name):
            return False
    except Exception:
        pass
    return True


_LISTENER_OFFLINE_HINT = (
    "This tool needs the UEFN editor listener (Deploy + open project). "
    "Do NOT retry it until the listener is online — it will keep failing. "
    "Verse file edits, digests, panel tools (ducky_*), and blender_* work without it."
)


def _looks_like_tool_failure(name: str, text: str) -> bool:
    """Avoid false failures when markdown/tool prose contains the word 'error'."""
    stripped = text.strip()
    if not stripped:
        return True
    if name in MARKDOWN_TOOLS:
        return stripped.startswith("ERROR:")
    if stripped.startswith("ERROR:"):
        return True
    from backend.agent.serialization import parse_tool_result_envelope

    obj = parse_tool_result_envelope(stripped)
    if isinstance(obj, dict):
        if obj.get("ok") is False:
            return True
        if obj.get("success") is False:
            return True
        if obj.get("error") and obj.get("success") is not True and obj.get("ok") is not True:
            return True
    return False


async def _await_cancellable(
    coro: Any,
    *,
    cancel_event: Any | None,
    timeout: float = 180.0,
) -> Any:
    """Await ``coro`` but bail as soon as ``cancel_event`` is set.

    Polls every 200ms so Stop interrupts long MCP/listener waits without
    waiting for the full tool timeout.
    """
    task = asyncio.ensure_future(coro)
    deadline = time.time() + max(timeout, 1.0)
    try:
        while True:
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.CancelledError("Cancelled")
            remaining = deadline - time.time()
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.TimeoutError(f"Tool timed out after {timeout}s")
            done, _ = await asyncio.wait({task}, timeout=min(0.2, remaining))
            if task in done:
                return task.result()
    except asyncio.CancelledError:
        raise
    except Exception:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        raise


async def execute_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    cancel_event: Any | None = None,
) -> ToolCallResult:
    args = arguments or {}
    t0 = time.time()
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        ms = int((time.time() - t0) * 1000)
        return ToolCallResult(ok=False, tool=name, error="Cancelled", duration_ms=ms)

    try:
        from backend.agent.coding_agents.plans import plan_mutator_block_reason

        blocked = plan_mutator_block_reason(name)
    except Exception:
        blocked = None
    if blocked:
        ms = int((time.time() - t0) * 1000)
        return ToolCallResult(
            ok=False,
            tool=name,
            error=blocked,
            hint="Call ducky_plan_update_node(node_id, status=\"in_progress\") first, then retry.",
            duration_ms=ms,
        )

    from backend.mcp_plugins.registry import PLUGIN_TOOL_SEP, is_plugin_tool
    from backend.mcp_plugins.store import ensure_plugin_prefix_cache

    if PLUGIN_TOOL_SEP in name:
        ensure_plugin_prefix_cache()
    if is_plugin_tool(name):
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            ms = int((time.time() - t0) * 1000)
            return ToolCallResult(ok=False, tool=name, error="Cancelled", duration_ms=ms)
        try:
            from backend.mcp_plugins.client_pool import get_plugin_pool

            text = await _await_cancellable(
                get_plugin_pool().call_tool(name, args),
                cancel_event=cancel_event,
                timeout=tool_timeout(name),
            )
            ms = int((time.time() - t0) * 1000)
            if _looks_like_tool_failure(name, text):
                return ToolCallResult(ok=False, tool=name, error=text[:8000], duration_ms=ms)
            return _with_plan_tick_nudge(
                name, ToolCallResult(ok=True, tool=name, data=text[:12000], duration_ms=ms)
            )
        except asyncio.CancelledError:
            ms = int((time.time() - t0) * 1000)
            return ToolCallResult(ok=False, tool=name, error="Cancelled", duration_ms=ms)
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            msg = str(e)
            return ToolCallResult(ok=False, tool=name, error=msg, hint=_hint_for_error(name, msg), duration_ms=ms)

    # Opt-in listener check: only editor tools. Host tools never probe UEFN.
    if _requires_uefn_listener(name):
        from backend.bridge import configured_listener_port, listener_get_health

        port = configured_listener_port()
        if listener_get_health(port, timeout=0.35) is None:  # fail-fast; never wait on REQUEST_TIMEOUT
            ms = int((time.time() - t0) * 1000)
            return ToolCallResult(
                ok=False,
                tool=name,
                error=f"UEFN listener offline on port {port}",
                hint=_LISTENER_OFFLINE_HINT,
                duration_ms=ms,
            )

    try:
        mcp = _ensure_mcp()
        raw = await _await_cancellable(
            mcp.call_tool(name, args),
            cancel_event=cancel_event,
            timeout=tool_timeout(name),
        )
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            ms = int((time.time() - t0) * 1000)
            return ToolCallResult(ok=False, tool=name, error="Cancelled", duration_ms=ms)
        text = _content_to_text(raw)
        ms = int((time.time() - t0) * 1000)
        if _looks_like_tool_failure(name, text):
            hint = _hint_for_error(name, text)
            return ToolCallResult(ok=False, tool=name, error=text[:8000], hint=hint, duration_ms=ms)
        return _with_plan_tick_nudge(
            name, ToolCallResult(ok=True, tool=name, data=text[:12000], duration_ms=ms)
        )
    except asyncio.CancelledError:
        ms = int((time.time() - t0) * 1000)
        return ToolCallResult(ok=False, tool=name, error="Cancelled", duration_ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        msg = str(e)
        return ToolCallResult(
            ok=False,
            tool=name,
            error=msg,
            hint=_hint_for_error(name, msg),
            duration_ms=ms,
        )
