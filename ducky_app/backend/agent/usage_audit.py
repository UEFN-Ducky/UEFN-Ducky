"""What a project's Claude Code chats cost, and where: read from Claude Code's own session logs.

Built from the Nov 17 2026 MovieTheater audit (92 chats, ~$2.4K at API prices), where 56% of
the spend was calls re-reading over 400K of context and 42% of reads repeated. Run it again
after a change to see whether those numbers moved.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# $ per million tokens: input, output, cache read. Cache writes are 1.25x input (5 min tier)
# or 2x input (1 h tier), read from each call's own cache_creation split.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.40),
    "claude-fable-5-2": (8.0, 40.0, 0.20),
}
BUCKETS = ((50_000, "<50K"), (100_000, "50-100K"), (200_000, "100-200K"), (400_000, "200-400K"))
_READS = ("workspace_read_file", "workspace_read_files", "view_image", "skill_read_subskill",
          "unreal__describe_toolset", "ducky_read_tool_spill", "get_verse_api", "search_verse_digest")


def claude_log_dir(project_root: str, home: str = "") -> Path:
    """Claude Code keeps a project's sessions in ~/.claude/projects/<path with : \\ / space as ->."""
    slug = re.sub(r"[:\\/ ]", "-", str(project_root).rstrip("\\/"))
    return Path(home or os.path.expanduser("~")) / ".claude" / "projects" / slug


def _bucket(ctx: int) -> str:
    return next((name for limit, name in BUCKETS if ctx < limit), "400K+")


def _call_cost(model: str, usage: dict[str, Any]) -> float:
    inp, out, read = PRICES[model]
    split = usage.get("cache_creation") or {}
    one_hour = int(split.get("ephemeral_1h_input_tokens") or 0)
    written = int(usage.get("cache_creation_input_tokens") or 0)
    return (int(usage.get("input_tokens") or 0) * inp + int(usage.get("output_tokens") or 0) * out
            + one_hour * inp * 2 + (written - one_hour) * inp * 1.25
            + int(usage.get("cache_read_input_tokens") or 0) * read) / 1e6


def _lines(path: Path) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _when(row: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(row.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def audit_logs(files: Iterable[Path], *, since: float = 0.0) -> dict[str, Any]:
    """Totals, cost by context size, repeat reads and the costliest chats for these session logs.
    One call is counted once by (message.id, requestId) and one tool call once by its id: the logs'
    ``api-request-blob`` rows copy earlier messages (Nov 17 2026: counting them made 2% repeat
    reads look like 42%). A compaction starts a new read ledger."""
    cost_by_bucket: Counter[str] = Counter()
    calls_by_bucket: Counter[str] = Counter()
    chats: list[dict[str, Any]] = []
    tokens = calls = reads = repeats = 0
    unpriced: Counter[str] = Counter()
    for path in files:
        seen: set[tuple[str, str]] = set()
        tool_ids: set[str] = set()
        read_keys: set[str] = set()
        title, cost, peak, n = "", 0.0, 0, 0
        for row in _lines(path):
            if row.get("isCompactSummary"):
                read_keys.clear()
            message = row.get("message")
            if not isinstance(message, dict) or row.get("type") == "api-request-blob":
                continue
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("id"):
                    if block["id"] in tool_ids:
                        continue
                    tool_ids.add(block["id"])
                name = str(block.get("name") or "").removeprefix("mcp__uefn__")
                args = block.get("input") or {}
                if not title and name.endswith("rename_self"):
                    title = str(args.get("title") or "")
                if name in _READS and _when(row) >= since:
                    key = name + json.dumps(args, sort_keys=True)
                    reads += 1
                    repeats += key in read_keys
                    read_keys.add(key)
            usage = message.get("usage")
            model = str(message.get("model") or "")
            if message.get("role") != "assistant" or not isinstance(usage, dict) or _when(row) < since:
                continue
            key = (str(message.get("id") or ""), str(row.get("requestId") or ""))
            if key in seen:
                continue
            seen.add(key)
            ctx = sum(int(usage.get(k) or 0) for k in
                      ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            tokens += ctx + int(usage.get("output_tokens") or 0)
            if model not in PRICES:
                unpriced[model] += 1
                continue
            price = _call_cost(model, usage)
            cost += price
            peak = max(peak, ctx)
            n += 1
            cost_by_bucket[_bucket(ctx)] += price
            calls_by_bucket[_bucket(ctx)] += 1
        if n:
            calls += n
            chats.append({"chat": title or path.stem[:8], "calls": n, "cost_usd": round(cost, 2),
                          "peak_context": peak})
    total = sum(cost_by_bucket.values())
    order = [name for _limit, name in BUCKETS] + ["400K+"]
    return {
        "chats": len(chats),
        "calls": calls,
        "tokens": tokens,
        "cost_usd": round(total, 2),
        "cost_by_context": {b: {"calls": calls_by_bucket[b], "cost_usd": round(cost_by_bucket[b], 2),
                                "share": round(cost_by_bucket[b] / total, 3) if total else 0.0} for b in order},
        "repeat_reads": {"repeats": repeats, "reads": reads, "share": round(repeats / reads, 3) if reads else 0.0},
        "top_chats": sorted(chats, key=lambda c: -c["cost_usd"])[:10],
        **({"unpriced_calls": dict(unpriced)} if unpriced else {}),
    }


def audit_project(project_root: str, days: int = 7, *, home: str = "", now: float | None = None) -> dict[str, Any]:
    folder = claude_log_dir(project_root, home)
    since = (time.time() if now is None else now) - max(1, int(days)) * 86400
    files = sorted(p for p in folder.glob("*.jsonl") if p.stat().st_mtime >= since) if folder.is_dir() else []
    out = audit_logs(files, since=since)
    out.update({"project": project_root, "days": max(1, int(days)), "logs": str(folder)})
    if not files:
        out["note"] = "No Claude Code session logs for this project in that window."
    return out
