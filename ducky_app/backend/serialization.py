"""TOON/JSON serialization for tool results sent to the embedded agent LLM."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

ToolResultFormat = Literal["toon", "json"]

_DEFAULT_FORMAT: ToolResultFormat = "toon"
_FORMAT_OVERRIDE: ToolResultFormat | None = None

_TOON_ENCODE_OPTS = {"delimiter": "\t", "indent": 2}

_MARKDOWN_TOOLS = frozenset({"uefn_skill"})
_API_TOOL_RESULT_MAX = 2200
# The mark must NOT instruct a blind retry: tools without pagination/filters
# (ping, status, most reads) return the identical oversized payload on retry,
# which drove an infinite re-call loop. Tell the model to stop and adapt instead.
_TRUNC_MARK = (
    "…[omitted: result too large for context. Do NOT repeat this call with the same "
    "arguments — it returns the same thing. Narrow the query, request specific "
    "fields/paths, or continue with what you already have.]"
)


def _tool_helpers() -> Any:
    import importlib

    return importlib.import_module("backend.agent.tools")


def set_tool_result_format(fmt: str | None) -> None:
    """Override format for the current agent run (from PanelSettings / RunConfig)."""
    global _FORMAT_OVERRIDE
    if fmt is None or not str(fmt).strip():
        _FORMAT_OVERRIDE = None
        return
    low = str(fmt).strip().lower()
    _FORMAT_OVERRIDE = "json" if low == "json" else "toon"


def tool_result_format() -> ToolResultFormat:
    if _FORMAT_OVERRIDE is not None:
        return _FORMAT_OVERRIDE
    env = os.environ.get("UEFN_DUCKY_TOOL_FORMAT", "").strip().lower()
    if env == "json":
        return "json"
    return _DEFAULT_FORMAT


# --- library with in-repo fallback ------------------------------------------
# PyPI toon_format 0.1.0 is a stub whose encode/decode raise NotImplementedError;
# the real implementation is the GitHub source. Fall back to a spec-subset
# encoder so TOON works (and the agent never crashes) regardless of which one
# the build resolved.

_LIB_ENCODE_OK: bool | None = None
_LIB_DECODE_OK: bool | None = None


def _encode_toon(obj: dict[str, Any]) -> str:
    global _LIB_ENCODE_OK
    if _LIB_ENCODE_OK is not False:
        try:
            from toon_format import encode

            out = encode(obj, _TOON_ENCODE_OPTS)
            _LIB_ENCODE_OK = True
            return out
        except Exception:
            _LIB_ENCODE_OK = False
    return _fallback_encode(obj)


def _decode_toon(text: str) -> Any:
    global _LIB_DECODE_OK
    if _LIB_DECODE_OK is not False:
        try:
            from toon_format import decode
            from toon_format.types import DecodeOptions

            out = decode(text, DecodeOptions(strict=False))
            _LIB_DECODE_OK = True
            return out
        except NotImplementedError:
            _LIB_DECODE_OK = False
        except ImportError:
            _LIB_DECODE_OK = False
    return _fallback_decode(text)


_PRIMS = (str, int, float, bool, type(None))


def _needs_quotes(s: str) -> bool:
    if s == "" or s.strip() != s:
        return True
    if any(c in ',:"\n\r\t#{}[]' for c in s):
        return True
    if s.lower() in ("true", "false", "null"):
        return True
    if s[0] in "-'\"":
        return True
    try:
        float(s)
        return True
    except ValueError:
        return False


def _scalar(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    return json.dumps(s, ensure_ascii=False) if _needs_quotes(s) else s


def _is_tabular(v: list) -> bool:
    if not v or not all(isinstance(x, dict) for x in v):
        return False
    keys = tuple(v[0].keys())
    if not keys:
        return False
    return all(
        tuple(x.keys()) == keys and all(isinstance(val, _PRIMS) for val in x.values())
        for x in v
    )


def _fallback_encode(obj: dict[str, Any], indent: int = 0) -> str:
    pad = "  " * indent
    lines: list[str] = []
    for k, v in obj.items():
        key = _scalar(k) if not isinstance(k, str) or _needs_quotes(k) else k
        if isinstance(v, dict):
            lines.append(f"{pad}{key}:")
            if v:
                lines.append(_fallback_encode(v, indent + 1))
        elif isinstance(v, list):
            if _is_tabular(v):
                fields = list(v[0].keys())
                lines.append(f"{pad}{key}[{len(v)}]{{{','.join(fields)}}}:")
                rpad = "  " * (indent + 1)
                for row in v:
                    lines.append(rpad + ",".join(_scalar(row[f]) for f in fields))
            elif all(isinstance(x, _PRIMS) for x in v):
                lines.append(f"{pad}{key}[{len(v)}]: " + ",".join(_scalar(x) for x in v))
            else:
                lines.append(f"{pad}{key}[{len(v)}]:")
                rpad = "  " * (indent + 1)
                for x in v:
                    if isinstance(x, dict):
                        lines.append(f"{rpad}-")
                        lines.append(_fallback_encode(x, indent + 2))
                    else:
                        lines.append(f"{rpad}- {_scalar(x)}")
        else:
            lines.append(f"{pad}{key}: {_scalar(v)}")
    return "\n".join(lines)


def _parse_scalar(tok: str) -> Any:
    t = tok.strip()
    if t.startswith('"'):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return t.strip('"')
    if t == "true":
        return True
    if t == "false":
        return False
    if t == "null":
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _split_row(line: str) -> list[str]:
    """Split a tabular row on commas outside double quotes."""
    out: list[str] = []
    cur = ""
    in_q = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_q = not in_q
        if ch == "," and not in_q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


def _fallback_decode(text: str) -> Any:
    """Minimal decoder for the envelope subset _fallback_encode produces."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    pos = [0]

    def parse_block(indent: int) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        while pos[0] < len(lines):
            line = lines[pos[0]]
            cur_indent = (len(line) - len(line.lstrip(" "))) // 2
            if cur_indent < indent:
                return obj
            stripped = line.strip()
            pos[0] += 1
            if "[" in stripped and "]" in stripped and stripped.endswith(":") and "{" in stripped:
                key = stripped.split("[", 1)[0]
                fields = stripped[stripped.index("{") + 1 : stripped.index("}")].split(",")
                n = int(stripped[stripped.index("[") + 1 : stripped.index("]")])
                rows = []
                for _ in range(n):
                    if pos[0] >= len(lines):
                        break
                    row = _split_row(lines[pos[0]].strip())
                    pos[0] += 1
                    rows.append({f: _parse_scalar(v) for f, v in zip(fields, row)})
                obj[key] = rows
            elif "[" in stripped and "]:" in stripped and not stripped.endswith(":"):
                key = stripped.split("[", 1)[0]
                inline = stripped.split("]:", 1)[1]
                obj[key] = [_parse_scalar(t) for t in _split_row(inline.strip())] if inline.strip() else []
            elif stripped.endswith(":"):
                key = stripped[:-1]
                if key.endswith("]") and "[" in key:
                    key = key.split("[", 1)[0]
                obj[key] = parse_block(indent + 1)
            elif ": " in stripped:
                key, _, val = stripped.partition(": ")
                obj[key] = _parse_scalar(val)
        return obj

    return parse_block(0)


def parse_tool_result_envelope(text: str) -> dict[str, Any] | None:
    """Parse a tool-result envelope from JSON or TOON. Returns None if not structured."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
    if ":" in stripped and not stripped.startswith("ERROR:"):
        try:
            obj = _decode_toon(stripped)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None
    return None


def _normalize_envelope(tool_name: str, obj: dict[str, Any]) -> dict[str, Any]:
    tools = _tool_helpers()
    out = dict(obj)
    data = out.get("data")
    if isinstance(data, str) and data.strip():
        try:
            inner = json.loads(data)
            out["data"] = tools.compact_json_value(tool_name, inner)
        except json.JSONDecodeError:
            pass
    elif isinstance(data, (dict, list)):
        out["data"] = tools.compact_json_value(tool_name, data)
    return out


def _cap_envelope(tool_name: str, obj: dict[str, Any], *, fmt: ToolResultFormat) -> str:
    tools = _tool_helpers()
    limit = tools.tool_result_max(tool_name)
    serialize = (
        (lambda o: json.dumps(o, ensure_ascii=False))
        if fmt == "json"
        else _encode_toon
    )
    working = dict(obj)
    out = serialize(working)
    for key in ("data", "error"):
        if len(out) <= limit:
            return out
        if isinstance(working.get(key), str) and tools.shrink_str_field(
            working, key, len(out) - limit
        ):
            out = serialize(working)
    if len(out) <= limit:
        return out
    # Structured data (dict/list) can't be char-sliced like a string, so before
    # discarding everything to a stub, shrink lists in place (keep the first N +
    # a truncation marker). This preserves scalar sibling fields and a usable
    # preview instead of handing the model an opaque "…" it can only retry.
    data = working.get("data")
    if isinstance(data, (list, dict)):
        working["data"] = tools.shrink_structured_value(data)
        out = serialize(working)
        if len(out) <= limit:
            return out
    stub: dict[str, Any]
    if working.get("ok"):
        stub = {"ok": True, "tool": working.get("tool", tool_name), "data": _TRUNC_MARK}
    else:
        stub = {"ok": working.get("ok"), "tool": working.get("tool", tool_name), "error": "result too large"}
    return serialize(stub)


def prepare_tool_result_envelope(tool_name: str, payload_json: str) -> dict[str, Any] | str:
    """Compact tool-result envelope; returns raw str when input is not JSON."""
    raw = (payload_json or "").strip()
    if not raw:
        return raw
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:_API_TOOL_RESULT_MAX] if len(raw) > _API_TOOL_RESULT_MAX else raw
    if not isinstance(obj, dict):
        return raw[:_API_TOOL_RESULT_MAX] if len(raw) > _API_TOOL_RESULT_MAX else raw
    return _normalize_envelope(tool_name, obj)


def format_tool_result_for_llm(
    tool_name: str,
    payload_json: str,
    *,
    fmt: ToolResultFormat | None = None,
) -> str:
    """Format a tool result for the LLM (TOON by default, JSON fallback)."""
    active = fmt or tool_result_format()
    if tool_name in _MARKDOWN_TOOLS:
        prepared = prepare_tool_result_envelope(tool_name, payload_json)
        if isinstance(prepared, dict):
            data = prepared.get("data")
            if data is not None:
                text = str(data)
                return text[:_API_TOOL_RESULT_MAX] if len(text) > _API_TOOL_RESULT_MAX else text
        raw = (payload_json or "").strip()
        return raw[:_API_TOOL_RESULT_MAX] if len(raw) > _API_TOOL_RESULT_MAX else raw

    if active == "json":
        from backend.agent.tools import compact_tool_result_for_api

        return compact_tool_result_for_api(tool_name, payload_json)

    prepared = prepare_tool_result_envelope(tool_name, payload_json)
    if isinstance(prepared, str):
        return prepared
    return _cap_envelope(tool_name, prepared, fmt="toon")


def format_tool_block_for_llm(block: dict[str, Any], *, fmt: ToolResultFormat | None = None) -> str:
    """Rebuild (or return stored) LLM-facing tool result content from a chat block."""
    stored = block.get("llm_content")
    if isinstance(stored, str) and stored.strip():
        return stored
    status = str(block.get("status") or "")
    ok = status == "success"
    payload: dict[str, Any] = {"ok": ok, "tool": block.get("name", "")}
    result = block.get("result")
    if isinstance(result, dict):
        if ok:
            payload["data"] = result.get("data", result)
        else:
            err = result.get("data") or result.get("error")
            payload["error"] = err if err is not None else str(result)
        hint = result.get("hint")
        if hint:
            payload["hint"] = hint
    elif result is not None:
        payload["data" if ok else "error"] = result
    return format_tool_result_for_llm(
        str(block.get("name") or ""),
        json.dumps(payload, ensure_ascii=False),
        fmt=fmt,
    )


def format_rejected_tool_result(error: str = "User rejected destructive action", *, fmt: ToolResultFormat | None = None) -> str:
    """Envelope for user-rejected destructive tool calls."""
    payload = {"ok": False, "error": error}
    active = fmt or tool_result_format()
    if active == "json":
        return json.dumps(payload, ensure_ascii=False)
    return _encode_toon(payload)


def _count_tokens(text: str, model: str = "", provider: str = "") -> int:
    raw = text or ""
    if not raw:
        return 0
    try:
        from frontend.ui_web.context_tokens import count_tokens

        return count_tokens(raw, model, provider)
    except Exception:
        return max(1, int(len(raw) / 3.2))


def count_tool_llm_tokens(llm_content: str, *, model: str = "", provider: str = "") -> int:
    """Token count of the payload actually sent to the model.

    Only the real sent payload is measured — we deliberately do NOT re-encode an
    alternate JSON form to compute a "savings vs JSON" figure. That comparison was
    surfaced in the UI (spammy, and it did an extra encode per tool call we never send).
    """
    return _count_tokens(llm_content, model, provider)
