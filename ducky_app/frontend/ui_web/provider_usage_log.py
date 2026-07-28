"""Global 7-day per-provider token usage ledger (JSONL under app data).

ponytail: single append-only JSONL file, prune on write — no DB. Upgrade path
is SQLite if call volume / concurrent writers becomes a problem.

Gateway streams auto-log via ``make_provider`` (see ``backend.agent.providers``).
Callers may set ``usage_*`` contextvars so chat/plugin attribution sticks.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir

_RETENTION_DAYS = 7
_LOG_NAME = "provider_usage.jsonl"

# Optional attribution for auto-logged gateway calls (copied into worker threads).
usage_agent: ContextVar[str] = ContextVar("usage_agent", default="")
usage_conv_id: ContextVar[str] = ContextVar("usage_conv_id", default="")
usage_ducky: ContextVar[str] = ContextVar("usage_ducky", default="")


def _log_path() -> Path:
    return default_app_data_dir() / _LOG_NAME


def _cutoff_ts(days: int) -> float:
    return time.time() - max(1, int(days)) * 86400.0


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _read_entries(path: Path, *, since: float) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    kept: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < since:
            continue
        kept.append(row)
    return kept


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in entries)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def log_call(
    *,
    provider: str,
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float | None = None,
    conv_id: str = "",
    agent: str = "",
    ducky_label: str = "",
    ts: float | None = None,
) -> None:
    """Append one API/coding-agent call; prune entries older than 7 days."""
    prov = str(provider or "").strip().lower()
    if not prov:
        return
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    cache_read = max(0, int(cache_read_tokens or 0))
    cache_write = max(0, int(cache_write_tokens or 0))
    if inp == 0 and out == 0 and cache_read == 0 and cache_write == 0:
        return

    now = float(ts) if ts is not None else time.time()
    entry: dict[str, Any] = {
        "ts": now,
        "provider": prov,
        "model": str(model or "").strip(),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "conv_id": str(conv_id or ""),
        "agent": str(agent or "").strip().lower(),
        "ducky_label": str(ducky_label or "").strip(),
    }
    if isinstance(cost_usd, (int, float)):
        entry["cost_usd"] = float(cost_usd)

    path = _log_path()
    since = _cutoff_ts(_RETENTION_DAYS)
    entries = _read_entries(path, since=since)
    entries.append(entry)
    _write_entries(path, entries)


def log_gateway_usage(
    *,
    provider: str,
    model: str = "",
    usage: dict[str, Any] | None = None,
    estimate_chars: int = 0,
    agent: str = "",
    conv_id: str = "",
    ducky_label: str = "",
    cost_usd: float | None = None,
) -> None:
    """Fail-soft ledger write for any gateway key use (always counts the call).

    Uses contextvars when attribution args are blank. Empty usage → estimate
    from ``estimate_chars`` or at least 1 token so the call still appears.
    """
    try:
        u = usage if isinstance(usage, dict) else {}
        inp = int(u.get("input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        cr = int(u.get("cache_read_tokens") or 0)
        cw = int(u.get("cache_write_tokens") or 0)
        if inp == 0 and out == 0 and cr == 0 and cw == 0:
            est = max(0, int(estimate_chars or 0))
            inp = max(1, est // 4) if est else 1
            out = 0
        log_call(
            provider=provider,
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            cost_usd=cost_usd,
            conv_id=str(conv_id or usage_conv_id.get() or ""),
            agent=str(agent or usage_agent.get() or ""),
            ducky_label=str(ducky_label or usage_ducky.get() or ""),
        )
    except Exception:
        pass


def bind_usage_context(
    *,
    agent: str = "",
    conv_id: str = "",
    ducky_label: str = "",
) -> tuple[Any, Any, Any]:
    """Set attribution contextvars; returns tokens for ``reset`` in a finally."""
    t_agent = usage_agent.set(str(agent or ""))
    t_conv = usage_conv_id.set(str(conv_id or ""))
    t_ducky = usage_ducky.set(str(ducky_label or ""))
    return t_agent, t_conv, t_ducky


def reset_usage_context(tokens: tuple[Any, Any, Any]) -> None:
    usage_agent.reset(tokens[0])
    usage_conv_id.reset(tokens[1])
    usage_ducky.reset(tokens[2])


def _empty_bucket() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "call_count": 0,
        "cost_usd": 0.0,
    }


def _add_to_bucket(bucket: dict[str, Any], *, inp: int, out: int, cr: int, cw: int, cost: float) -> None:
    bucket["input_tokens"] += inp
    bucket["output_tokens"] += out
    bucket["cache_read_tokens"] += cr
    bucket["cache_write_tokens"] += cw
    bucket["call_count"] += 1
    bucket["cost_usd"] += float(cost)


def _conv_lookup() -> dict[str, dict[str, str]]:
    """Map conv_id → {title, ducky_name, agent} for report labels (fail-soft)."""
    try:
        from frontend.ui_web.project_chats import list_all_conversation_metadata

        out: dict[str, dict[str, str]] = {}
        for c in list_all_conversation_metadata():
            cid = str(getattr(c, "id", "") or "")
            if not cid:
                continue
            out[cid] = {
                "title": str(getattr(c, "title", "") or ""),
                "ducky_name": str(getattr(c, "ducky_name", "") or ""),
                "agent": str(getattr(c, "coding_agent", "") or "").strip().lower(),
            }
        return out
    except Exception:
        return {}


def usage_report(provider_id: str = "", days: int = 7) -> dict[str, Any]:
    """Aggregate ledger entries for one provider (or all when provider_id blank)."""
    from backend.agent.model_pricing import call_cost_usd

    days_n = max(1, min(30, int(days or _RETENTION_DAYS)))
    since = _cutoff_ts(days_n)
    prov_filter = str(provider_id or "").strip().lower()
    entries = [
        e
        for e in _read_entries(_log_path(), since=since)
        if not prov_filter or str(e.get("provider") or "").strip().lower() == prov_filter
    ]
    conv_meta = _conv_lookup()

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    cost_sum = 0.0
    cost_known = False
    by_day: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    by_ducky: dict[str, dict[str, Any]] = {}

    # Ensure a contiguous day series for charts (oldest → newest).
    today = datetime.now(tz=timezone.utc).date()
    for i in range(days_n - 1, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        by_day[key] = {"date": key, **_empty_bucket()}

    for e in entries:
        inp = max(0, int(e.get("input_tokens") or 0))
        out = max(0, int(e.get("output_tokens") or 0))
        cr = max(0, int(e.get("cache_read_tokens") or 0))
        cw = max(0, int(e.get("cache_write_tokens") or 0))
        total_input += inp
        total_output += out
        total_cache_read += cr
        total_cache_write += cw

        cost = call_cost_usd(
            e,
            fallback_provider=str(e.get("provider") or prov_filter),
            fallback_model=str(e.get("model") or ""),
        )
        if cost is not None:
            cost_known = True
            cost_sum += float(cost)
        else:
            cost = 0.0

        day = _day_key(float(e.get("ts") or 0))
        day_row = by_day.get(day)
        if day_row is None:
            day_row = {"date": day, **_empty_bucket()}
            by_day[day] = day_row
        _add_to_bucket(day_row, inp=inp, out=out, cr=cr, cw=cw, cost=float(cost))

        model = str(e.get("model") or "").strip() or "(unknown)"
        model_row = by_model.get(model)
        if model_row is None:
            model_row = {"model": model, **_empty_bucket()}
            by_model[model] = model_row
        _add_to_bucket(model_row, inp=inp, out=out, cr=cr, cw=cw, cost=float(cost))

        conv_id = str(e.get("conv_id") or "").strip()
        meta = conv_meta.get(conv_id) if conv_id else None
        agent = (
            str(e.get("agent") or "").strip().lower()
            or (meta.get("agent") if meta else "")
            or "(unknown)"
        )
        agent_row = by_agent.get(agent)
        if agent_row is None:
            agent_row = {"agent": agent, **_empty_bucket()}
            by_agent[agent] = agent_row
        _add_to_bucket(agent_row, inp=inp, out=out, cr=cr, cw=cw, cost=float(cost))

        ducky_key = conv_id or "(no chat)"
        ducky_row = by_ducky.get(ducky_key)
        if ducky_row is None:
            label = str(e.get("ducky_label") or "").strip()
            if meta:
                label = meta.get("ducky_name") or meta.get("title") or label
            ducky_row = {
                "conv_id": conv_id,
                "label": label or (conv_id[:8] if conv_id else "(no chat)"),
                **_empty_bucket(),
            }
            by_ducky[ducky_key] = ducky_row
        _add_to_bucket(ducky_row, inp=inp, out=out, cr=cr, cw=cw, cost=float(cost))

    cache_hit_rate = 0.0
    if total_input > 0:
        cache_hit_rate = min(100.0, round((total_cache_read / total_input) * 100.0, 1))

    def _tok_rank(r: dict[str, Any]) -> int:
        return int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)

    return {
        "provider": prov_filter,
        "days": days_n,
        "call_count": len(entries),
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_input + total_output,
        "total_cache_read": total_cache_read,
        "total_cache_write": total_cache_write,
        "cache_hit_rate": cache_hit_rate,
        "cost_usd": cost_sum if cost_known else None,
        "by_day": sorted(by_day.values(), key=lambda r: r["date"]),
        "by_model": sorted(by_model.values(), key=_tok_rank, reverse=True),
        "by_agent": sorted(by_agent.values(), key=_tok_rank, reverse=True),
        "by_ducky": sorted(by_ducky.values(), key=_tok_rank, reverse=True),
    }


def ducky_usage_report(
    ducky_name: str,
    *,
    profile_id: str = "",
    days: int = 7,
) -> dict[str, Any]:
    """Usage + chat count for one agent-profile / ducky identity (last N days).

    Prefer ``profile_id`` on the conversation (stable across renames / duplicate
    names). Fall back to ``ducky_name`` (casefold) or group-member ``profile_id``.
    Ledger rows match those chat ids or the same ducky label.
    """
    from backend.agent.model_pricing import call_cost_usd

    name = str(ducky_name or "").strip()
    name_key = name.casefold()
    pid = str(profile_id or "").strip()
    days_n = max(1, min(30, int(days or _RETENTION_DAYS)))
    since = _cutoff_ts(days_n)

    chats: list[dict[str, Any]] = []
    conv_ids: set[str] = set()
    try:
        from frontend.ui_web.project_chats import list_all_conversation_metadata

        for c in list_all_conversation_metadata():
            cid = str(getattr(c, "id", "") or "")
            if not cid:
                continue
            conv_pid = str(getattr(c, "profile_id", "") or "").strip()
            dn = str(getattr(c, "ducky_name", "") or "").strip()
            match = bool(pid) and conv_pid == pid
            if not match and bool(name_key) and not pid:
                # Name fallback only when caller did not pass a profile id —
                # avoids merging two same-named agents' chats.
                match = dn.casefold() == name_key
            if not match and pid:
                members = getattr(c, "group_members", None) or []
                if isinstance(members, list):
                    match = any(
                        str(m.get("profile_id") or "").strip() == pid
                        for m in members
                        if isinstance(m, dict)
                    )
            if not match:
                continue
            conv_ids.add(cid)
            chats.append(
                {
                    "conv_id": cid,
                    "title": str(getattr(c, "title", "") or "") or dn or cid[:8],
                    "updated": float(getattr(c, "updated", 0) or 0),
                }
            )
    except Exception:
        pass

    entries = [
        e
        for e in _read_entries(_log_path(), since=since)
        if (
            (str(e.get("conv_id") or "").strip() in conv_ids)
            or (name_key and str(e.get("ducky_label") or "").strip().casefold() == name_key)
        )
    ]

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    cost_sum = 0.0
    cost_known = False
    for e in entries:
        cid = str(e.get("conv_id") or "").strip()
        if cid:
            conv_ids.add(cid)
        inp = max(0, int(e.get("input_tokens") or 0))
        out = max(0, int(e.get("output_tokens") or 0))
        cr = max(0, int(e.get("cache_read_tokens") or 0))
        cw = max(0, int(e.get("cache_write_tokens") or 0))
        total_input += inp
        total_output += out
        total_cache_read += cr
        total_cache_write += cw
        cost = call_cost_usd(
            e,
            fallback_provider=str(e.get("provider") or ""),
            fallback_model=str(e.get("model") or ""),
        )
        if cost is not None:
            cost_known = True
            cost_sum += float(cost)

    # Prefer metadata chat list; fall back to distinct ledger conv ids.
    if not chats and conv_ids:
        chats = [{"conv_id": cid, "title": cid[:8], "updated": 0.0} for cid in sorted(conv_ids)]

    chats.sort(key=lambda r: float(r.get("updated") or 0), reverse=True)
    return {
        "ducky_name": name,
        "profile_id": pid,
        "days": days_n,
        "chat_count": len(chats),
        "call_count": len(entries),
        "total_input": total_input,
        "total_output": total_output,
        "total_tokens": total_input + total_output,
        "total_cache_read": total_cache_read,
        "total_cache_write": total_cache_write,
        "cost_usd": cost_sum if cost_known else None,
        "chats": chats[:50],
    }
