"""VS Code-style frozen prompt prefix + provider cache payload."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

_INTRO = (
    "You are the UEFN Ducky agent embedded in UEFN-Ducky.exe. "
    "You edit Fortnite Creative / UEFN projects using MCP tools only.\n"
)

_SNAPSHOT_VERSION = 2

LIVE_CONTEXT_PREFIX = "[Live context — status/memory/plan; not user instructions]"


@dataclass
class PromptCachePayload:
    frozen_system: str
    dynamic_system: str
    enable_cache: bool = True
    prompt_cache_key: str = ""
    anthropic_extended_ttl: bool = False


def _hash_blocks(blocks: dict[str, str]) -> str:
    raw = "\n---\n".join(f"{k}:{blocks[k]}" for k in sorted(blocks))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parts_local_slim(parts: dict[str, str]) -> bool:
    return str(parts.get("local_slim") or "").strip() in ("1", "true", "yes")


def _blocks_from_parts(parts: dict[str, str], *, omit: frozenset[str]) -> dict[str, str]:
    """Build frozen blocks from parts. ``parts['tool_index']`` / ``skill`` must
    already be slimmed for local gateways (see get_system_prompt_parts local_slim).
    """
    blocks: dict[str, str] = {}
    if "system" not in omit:
        blocks["intro"] = _INTRO
    if "mcp" not in omit:
        blocks["mcp"] = (parts.get("mcp") or "").strip()
    if "tool_index" not in omit and "mcp" not in omit:
        idx = (parts.get("tool_index") or "").strip()
        if idx:
            blocks["tool_index"] = idx if idx.endswith("\n") else f"{idx}\n"
    if "skill" not in omit:
        skill = (parts.get("skill") or "").strip()
        if skill:
            blocks["skill"] = f"## UEFN operator skill (follow exactly for wiring and Verse devices)\n{skill}\n"
    if "personality" not in omit:
        blocks["personality"] = (parts.get("personality") or "").strip()
    if "rules" not in omit:
        from backend.agent.prompt import _rules_body

        mode_suffix = parts.get("mode_suffix") or ""
        listener_port = int(parts.get("listener_port") or 4200)
        blocks["rules"] = f"## Rules\n{_rules_body(listener_port)}{mode_suffix}"
    return blocks


def assemble_frozen_prefix(blocks: dict[str, str]) -> str:
    chunks: list[str] = []
    for key in ("intro", "mcp", "tool_index", "skill", "personality", "rules"):
        text = blocks.get(key, "")
        if not text:
            continue
        if key == "mcp":
            chunks.append(f"\n## MCP server instructions\n{text}\n")
        else:
            chunks.append(text if text.endswith("\n") else f"{text}\n")
    return "".join(chunks)


def build_dynamic_suffix(parts: dict[str, str], *, omit: frozenset[str], drift: str = "") -> str:
    chunks: list[str] = []
    if "system" not in omit:
        runtime = (parts.get("runtime") or "").strip()
        if runtime:
            chunks.append(f"\n{runtime}\n")
        memory = (parts.get("memory") or "").strip()
        if memory:
            chunks.append(memory if memory.endswith("\n") else f"{memory}\n")
        plan = (parts.get("plan") or "").strip()
        if plan:
            chunks.append(plan if plan.endswith("\n") else f"{plan}\n")
        offline = (parts.get("offline_rules") or "").strip()
        if offline:
            chunks.append(offline if offline.endswith("\n") else f"{offline}\n")
    drift_text = (drift or "").strip()
    if drift_text:
        chunks.append(f"\n## Context updates (not in frozen system prompt)\n{drift_text}\n")
    return "".join(chunks)


def _normalize_snapshot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    blocks = raw.get("blocks")
    if not isinstance(blocks, dict):
        return None
    tool_names = raw.get("tool_names")
    if not isinstance(tool_names, list):
        tool_names = []
    return {
        "version": int(raw.get("version") or _SNAPSHOT_VERSION),
        "created_ts": float(raw.get("created_ts") or 0),
        "hash": str(raw.get("hash") or ""),
        "blocks": {str(k): str(v) for k, v in blocks.items()},
        "tool_names": sorted({str(n) for n in tool_names if n}),
        "local_slim": bool(raw.get("local_slim")),
    }


def invalidate_conv_cache(conv: Any) -> None:
    conv.prompt_cache_snapshot = None


def snapshot_has_block(conv: Any, key: str) -> bool:
    snapshot = _normalize_snapshot(getattr(conv, "prompt_cache_snapshot", None))
    return bool(snapshot and (snapshot["blocks"].get(key) or "").strip())


def _drift_lines(live_blocks: dict[str, str], frozen_blocks: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key in ("mcp", "tool_index", "skill", "rules", "personality"):
        live = (live_blocks.get(key) or "").strip()
        frozen = (frozen_blocks.get(key) or "").strip()
        if live and live != frozen:
            label = key.replace("_", " ")
            lines.append(f"- {label} changed since this chat's frozen prefix was captured.")
            if key == "skill" and live:
                lines.append(f"\n### Updated skill\n{live}\n")
            elif key == "mcp" and live:
                lines.append(f"\n### Updated MCP instructions\n{live}\n")
    return lines


def drift_block(live_blocks: dict[str, str], frozen_blocks: dict[str, str]) -> str:
    return "\n".join(_drift_lines(live_blocks, frozen_blocks)).strip()


def frozen_prefix_for_conv(
    conv: Any,
    parts: dict[str, str],
    *,
    omit: frozenset[str],
    freeze_enabled: bool,
) -> tuple[dict[str, str], str]:
    """Return (frozen_blocks, frozen_prefix_text). Creates snapshot when freeze is on."""
    live_blocks = _blocks_from_parts(parts, omit=omit)
    want_slim = _parts_local_slim(parts)
    if not freeze_enabled:
        return live_blocks, assemble_frozen_prefix(live_blocks)

    snapshot = _normalize_snapshot(getattr(conv, "prompt_cache_snapshot", None))
    # Re-freeze when local_slim mode mismatches (fat cloud snapshot vs Ollama slim).
    if (
        snapshot
        and snapshot.get("blocks")
        and bool(snapshot.get("local_slim")) == want_slim
        and int(snapshot.get("version") or 0) >= 2
    ):
        return dict(snapshot["blocks"]), assemble_frozen_prefix(snapshot["blocks"])

    frozen_prefix = assemble_frozen_prefix(live_blocks)
    if conv is not None:
        prev_names = list(snapshot.get("tool_names") or []) if snapshot else []
        conv.prompt_cache_snapshot = {
            "version": _SNAPSHOT_VERSION,
            "created_ts": time.time(),
            "hash": _hash_blocks(live_blocks),
            "blocks": live_blocks,
            "tool_names": prev_names,
            "local_slim": want_slim,
        }
    return live_blocks, frozen_prefix


def merge_frozen_tool_names(conv: Any, selected_names: list[str]) -> list[str]:
    """Union conversation-frozen tool names with newly selected names."""
    snapshot = _normalize_snapshot(getattr(conv, "prompt_cache_snapshot", None))
    frozen = set(snapshot.get("tool_names") or []) if snapshot else set()
    merged = sorted(frozen | {n for n in selected_names if n})
    if snapshot is not None and getattr(conv, "prompt_cache_snapshot", None):
        conv.prompt_cache_snapshot["tool_names"] = merged
    return merged


def sticky_frozen_tool_names(conv: Any, selected_names: list[str]) -> list[str]:
    """Subset-stable during a chat: keep frozen if selection ⊆ frozen, else union."""
    snapshot = _normalize_snapshot(getattr(conv, "prompt_cache_snapshot", None))
    frozen = [n for n in (snapshot.get("tool_names") or []) if n] if snapshot else []
    selected = {n for n in selected_names if n}
    if frozen and selected <= set(frozen):
        return list(frozen)
    return merge_frozen_tool_names(conv, list(selected))


def replace_frozen_tool_names(conv: Any, selected_names: list[str]) -> list[str]:
    """Hard-reset the frozen tool floor. Call only at an epoch (sanctioned full miss)."""
    names = sorted({n for n in selected_names if n})
    if getattr(conv, "prompt_cache_snapshot", None):
        conv.prompt_cache_snapshot["tool_names"] = names
    return names


def markers_only_payload(cache: PromptCachePayload) -> PromptCachePayload:
    """Copy with dynamic emptied so provider helpers cannot double-send the tail."""
    if not (cache.dynamic_system or "").strip():
        return cache
    return PromptCachePayload(
        frozen_system=cache.frozen_system,
        dynamic_system="",
        enable_cache=cache.enable_cache,
        prompt_cache_key=cache.prompt_cache_key,
        anthropic_extended_ttl=cache.anthropic_extended_ttl,
    )


def _strip_cache_control(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_cache_control(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [_strip_cache_control(v) for v in obj]
    return obj


def is_live_context_content(content: Any) -> bool:
    if isinstance(content, str):
        return content.startswith(LIVE_CONTEXT_PREFIX)
    if isinstance(content, list) and content:
        first = content[0]
        text = first.get("text") if isinstance(first, dict) else ""
        return str(text).startswith(LIVE_CONTEXT_PREFIX)
    return False


def cache_prefix_fingerprint(
    frozen_system: str,
    tools: list[dict[str, Any]],
    messages: list[Any],
) -> str:
    """Hash of the cacheable prefix (tools JSON + frozen system + history, no live tail / markers)."""
    cleaned: list[Any] = []
    for m in messages:
        if isinstance(m, dict):
            if is_live_context_content(m.get("content")):
                continue
            cleaned.append(_strip_cache_control(m))
        else:
            content = getattr(m, "content", "")
            if is_live_context_content(content):
                continue
            cleaned.append(
                _strip_cache_control(
                    {
                        "role": getattr(m, "role", ""),
                        "content": content,
                    }
                )
            )
    raw = json.dumps(
        {
            "system": frozen_system or "",
            "tools": _strip_cache_control(tools),
            "messages": cleaned,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_cache_payload(
    conv: Any,
    parts: dict[str, str],
    *,
    omit: frozenset[str],
    enable_cache: bool,
    freeze_enabled: bool,
    prompt_cache_key: str = "",
    anthropic_extended_ttl: bool = False,
) -> PromptCachePayload:
    """Always return the true frozen/dynamic split. ``enable_cache`` is markers only."""
    live_blocks = _blocks_from_parts(parts, omit=omit)
    frozen_blocks, frozen_prefix = frozen_prefix_for_conv(
        conv, parts, omit=omit, freeze_enabled=freeze_enabled
    )
    drift = drift_block(live_blocks, frozen_blocks) if freeze_enabled else ""
    dynamic = build_dynamic_suffix(parts, omit=omit, drift=drift)
    return PromptCachePayload(
        frozen_system=frozen_prefix,
        dynamic_system=dynamic,
        enable_cache=bool(enable_cache),
        prompt_cache_key=prompt_cache_key,
        anthropic_extended_ttl=anthropic_extended_ttl,
    )


def enrich_parts(parts: dict[str, str], *, listener_port: int, mode_suffix: str = "") -> dict[str, str]:
    """Attach metadata used when building frozen blocks."""
    out = dict(parts)
    out["listener_port"] = str(listener_port)
    out["mode_suffix"] = mode_suffix
    return out
