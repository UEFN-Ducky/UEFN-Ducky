"""Host-side stale tool-result clearing — gateway-neutral, before the provider call.

Mirrors Anthropic clear_tool_uses / Claude Code microcompact (trigger / keep /
clear_at_least / exclude_tools) without any provider names in this module.
Cache behaviour is read from the gateway plugin's register_llm_provider extras:
``cache_mode`` and optional ``cache_ttl_s``.
"""

from __future__ import annotations

import json
import time

from backend.agent.context_memory import estimate_tokens
from backend.agent.providers.base import ProviderMessage

# ponytail: fixed fractions; upgrade = Settings knobs if users need to tune.
TRIGGER_FRACTION = 0.5
TARGET_FRACTION = 0.25
KEEP_RECENT_TOOL_RESULTS = 5
EXCLUDE_TOOLS = frozenset({"ducky_ask_user"})
MIN_CLEARABLE_CHARS = 200
IMAGE_TOKEN_ESTIMATE = 1600
CLEARED_MARKER = "[Old tool result cleared"
IMAGE_CLEARED = "[image cleared]"


def high_water_thresholds(high_water: int) -> tuple[int, int]:
    hw = max(1, int(high_water or 0))
    return max(1, int(hw * TRIGGER_FRACTION)), max(0, int(hw * TARGET_FRACTION))


def cleared_stub(ok: bool, tool: str, chars: int, *, conv_id: str = "") -> str:
    cid = (conv_id or "").strip()
    hint = (
        f"Re-call the tool, or ducky_read_chat({cid!r}) for the original result."
        if cid
        else "Re-call the tool if you need it again."
    )
    payload = json.dumps(
        {
            "ok": bool(ok),
            "tool": tool or "tool",
            "data": f"{CLEARED_MARKER} ({int(chars)} chars). {hint}]",
        },
        ensure_ascii=False,
    )
    try:
        from backend.agent.serialization import format_tool_result_for_llm

        return format_tool_result_for_llm(tool or "tool", payload, fmt=None)
    except Exception:
        return payload


def is_cleared_text(text: str) -> bool:
    raw = text or ""
    return CLEARED_MARKER in raw or IMAGE_CLEARED in raw


def plan_clears(
    sizes: list[int],
    *,
    keep_recent: int,
    trigger: int,
    target: int,
    force: bool = False,
    budget: int | None = None,
) -> set[int]:
    """Indices (oldest first) to stub.

    Empty unless ``budget`` (or ``sum(sizes)``) >= trigger (or force). Stops once
    remaining live sizes <= target. Skips if it cannot free at least
    (trigger - target) — not worth breaking a prefix cache. ``force`` skips both
    the trigger and the clear-at-least floor.
    """
    n = len(sizes)
    keep = max(0, int(keep_recent))
    if n <= keep:
        return set()
    total = sum(max(0, int(s)) for s in sizes)
    used = total if budget is None else max(total, int(budget))
    tgt = max(0, int(target))
    trig = max(0, int(trigger))
    if not force and used < trig:
        return set()
    if total <= tgt:
        return set()

    chosen: set[int] = set()
    remaining = total
    freed = 0
    for i in range(n - keep):
        if remaining <= tgt:
            break
        size = max(0, int(sizes[i]))
        chosen.add(i)
        remaining -= size
        freed += size
    if not force:
        min_free = max(0, trig - tgt)
        if freed < min_free:
            return set()
    return chosen


def should_force_clear(provider: str, last_message_ts: float) -> bool:
    """True when there is no explicit prefix cache to protect, or it already expired.

    Reads only ``cache_mode`` / ``cache_ttl_s`` from the gateway plugin
    registration — no provider-name branches in core.
    """
    mode = ""
    ttl_s: int | None = None
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        reg = get_llm_provider_registration(provider) or {}
        mode = str(reg.get("cache_mode") or "").strip().lower()
        raw = reg.get("cache_ttl_s")
        if callable(raw):
            raw = raw()
        if raw is not None and str(raw).strip() != "":
            ttl_s = int(raw)
    except Exception:
        mode = ""
        ttl_s = None
    if mode != "cached":
        return True
    if ttl_s is None:
        return False
    if last_message_ts <= 0:
        return False
    return (time.time() - float(last_message_ts)) > ttl_s


def _tool_names(msgs: list[ProviderMessage]) -> dict[str, str]:
    names: dict[str, str] = {}
    for m in msgs:
        if str(m.role or "") != "assistant":
            continue
        for tc in m.tool_calls or []:
            tid = str(getattr(tc, "id", "") or "").strip()
            name = str(getattr(tc, "name", "") or "").strip()
            if tid and name:
                names[tid] = name
    return names


def _tool_meta(content: str, fallback_name: str) -> tuple[bool, str]:
    from backend.agent.serialization import parse_tool_result_envelope

    obj = parse_tool_result_envelope(content) or {}
    name = str(obj.get("tool") or "").strip() or fallback_name
    ok = bool(obj.get("ok", True)) if obj else True
    return ok, name


def _is_capture_user(m: ProviderMessage) -> bool:
    if str(m.role or "") != "user":
        return False
    if not m.attachments:
        return False
    return str(m.content or "").lstrip().startswith("[capture attached")


def clear_stale_tool_results(
    msgs: list[ProviderMessage],
    *,
    high_water: int,
    prompt_tokens: int = 0,
    force: bool = False,
    conv_id: str = "",
) -> int:
    """Stub old tool results / capture images in place on the ephemeral per-turn list.

    Never mutates stored ``conv.messages`` / ``assistant_blocks``. Leaves
    ``tool_call_id``, assistant ``tool_calls``, and ``thinking_blocks`` intact.
    Returns estimated tokens freed.
    """
    if not msgs:
        return 0
    names = _tool_names(msgs)
    items: list[tuple[int, int, str]] = []  # (msg_index, size, kind) kind=tool|image
    for i, m in enumerate(msgs):
        role = str(m.role or "")
        if role == "tool":
            content = str(m.content or "")
            if is_cleared_text(content) or len(content) < MIN_CLEARABLE_CHARS:
                continue
            name = names.get(str(m.tool_call_id or ""), "")
            if name in EXCLUDE_TOOLS:
                continue
            items.append((i, estimate_tokens(content), "tool"))
            continue
        if _is_capture_user(m) and not is_cleared_text(str(m.content or "")):
            n_img = sum(1 for a in (m.attachments or []) if str(getattr(a, "kind", "") or "") == "image")
            if n_img <= 0:
                continue
            items.append((i, n_img * IMAGE_TOKEN_ESTIMATE, "image"))

    if not items:
        return 0
    trigger, target = high_water_thresholds(high_water)
    budget = int(prompt_tokens) if prompt_tokens > 0 else None
    chosen = plan_clears(
        [size for _, size, _ in items],
        keep_recent=KEEP_RECENT_TOOL_RESULTS,
        trigger=trigger,
        target=target,
        force=force,
        budget=budget,
    )
    if not chosen:
        return 0

    freed = 0
    for item_i in sorted(chosen):
        msg_i, size, kind = items[item_i]
        m = msgs[msg_i]
        if kind == "tool":
            content = str(m.content or "")
            ok, tool = _tool_meta(content, names.get(str(m.tool_call_id or ""), "tool"))
            stub = cleared_stub(ok, tool, len(content), conv_id=conv_id)
            freed += max(0, size - estimate_tokens(stub))
            m.content = stub
        else:
            stub = IMAGE_CLEARED
            freed += max(0, size - estimate_tokens(stub))
            m.content = stub
            m.attachments = []
    return freed
