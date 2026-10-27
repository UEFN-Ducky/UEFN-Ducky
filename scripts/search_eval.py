"""Score skill and tool search against the questions in ``backend/search/eval/``.

Reads the skills and plugins installed on this PC (read-only) and the live tool
catalog of the running app, builds a throwaway search index in a temp folder, and
prints hit@1, hit@3 and MRR (first hit in the top 10) per question set.

Engines:

* ``today``   — what agents had before: a skill is found when the question's words
  hit its pack/sub-skill id or label (the prompt index); tools use the old
  all-words ``search_tool_catalog``.
* ``keyword`` — the new index, FTS5 bm25 only.
* ``hybrid``  — the new index, keywords + meaning (what agents use).

Usage::

    python scripts/search_eval.py --engine hybrid [--json] [--misses]
    python scripts/search_eval.py --engine all
    python scripts/search_eval.py --tools-json catalog.json   # offline: a saved get_mcp_tools_catalog result
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "ducky_app" / "backend" / "search" / "eval"
TOKEN = re.compile(r"[^\W_]+")


def _drop_scratch(scratch: Path) -> None:
    """Close the throwaway index and delete it; each run used to leave ~14 MB in Temp."""
    try:
        from backend.store import db

        db.reset_for_tests()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(scratch, ignore_errors=True)


def _load(name: str) -> list[dict]:
    lines = (EVAL_DIR / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _live_catalog(port: int) -> list[dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/__panel_api/get_mcp_tools_catalog",
        data=b'{"args": []}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return list((data.get("result") or {}).get("tools") or [])


def _corpus_packs(appdata: Path, repos: Path | None = None) -> list[tuple[str, Path, str, str]]:
    """Installed plugin packs (a plugin's source repo under ``repos`` wins) and AppData packs."""
    out: list[tuple[str, Path, str, str]] = []
    plugins = appdata / "uefn_plugins"
    if plugins.is_dir():
        for plugin in sorted(plugins.iterdir()):
            skills = plugin / "skills"
            repo = (repos / f"uefn-plugin-{plugin.name}") if repos else None
            if repo is not None and (repo / "skills").is_dir():
                skills = repo / "skills"
            if not skills.is_dir():
                continue
            for pack in sorted(skills.iterdir()):
                if (pack / "SKILL.md").is_file():
                    out.append((pack.name, pack, "plugin", plugin.name))
    packs = appdata / "skill_packs"
    owned = {p for p, *_ in out}
    if packs.is_dir():
        for pack in sorted(packs.iterdir()):
            if (pack / "SKILL.md").is_file() and pack.name not in owned:
                repo = (repos / f"uefn-skill-{pack.name}") if repos else None
                out.append((pack.name, repo if repo is not None and (repo / "SKILL.md").is_file() else pack, "user", ""))
    return out


def _skill_key(row: dict) -> str:
    sub = row.get("subskill") or ("core" if row.get("kind") == "pack" else "")
    return f"{row.get('pack')}/{sub}"


def _rank(results: list[str], expect: list[str]) -> int | None:
    for i, key in enumerate(results[:10]):
        if key in expect:
            return i + 1
    return None


def _score(items: list[dict], ranker) -> dict:
    hits1 = hits3 = 0
    rr = 0.0
    misses = []
    for item in items:
        got = ranker(item["q"])
        rank = _rank(got, item["expect"])
        if rank == 1:
            hits1 += 1
        if rank is not None and rank <= 3:
            hits3 += 1
        rr += (1.0 / rank) if rank else 0.0
        if rank is None or rank > 3:
            misses.append({"q": item["q"], "expect": item["expect"], "got": got[:5], "rank": rank})
    n = max(1, len(items))
    return {"n": len(items), "hit@1": round(hits1 / n, 3), "hit@3": round(hits3 / n, 3), "mrr": round(rr / n, 3), "misses": misses}


def _hint_score(skills: list[dict], items: list[dict], ctx) -> dict:
    """What the per-message skill hint does: how many real requests get one (and the
    expected section among them), and how many chat lines about Ducky itself get one."""
    from backend.search import hints, query

    def hinted(q: str) -> list[dict]:
        return [r for r in query.search(hints._focus(q)[:hints.QUERY_CHARS], limit=3, context=ctx, refresh=False)
                if hints._relevant(r)]

    requests = [(i["q"], i.get("expect") or []) for i in skills] + [(i["q"], []) for i in items if i["hint"]]
    chat = [i["q"] for i in items if not i["hint"]]
    got = right = 0
    wrong: list[str] = []
    for q, expect in requests:
        rows = hinted(q)
        got += bool(rows)
        right += bool(expect) and any(_skill_key(r) in expect for r in rows)
        if not rows:
            wrong.append(f"no hint: {q}")
    chatty = 0
    for q in chat:
        rows = hinted(q)
        chatty += bool(rows)
        if rows:
            wrong.append(f"hinted chat: {q} -> {[_skill_key(r) for r in rows]}")
    with_expect = max(1, sum(1 for _, e in requests if e))
    return {"requests_hinted": round(got / max(1, len(requests)), 3), "right_section": round(right / with_expect, 3),
            "chat_hinted": round(chatty / max(1, len(chat)), 3), "wrong": wrong}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--engine", default="all", choices=("today", "keyword", "hybrid", "all"))
    ap.add_argument("--appdata", default="", help="UEFN-Ducky AppData folder to read skills from (read-only)")
    ap.add_argument("--tools-json", default="", help="a saved get_mcp_tools_catalog result instead of the live app")
    ap.add_argument("--port", type=int, default=4199, help="the running app's panel HTTP port")
    ap.add_argument("--repos", default="", help="folder of plugin repos (uefn-plugin-<id>) whose skills replace the installed ones")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--misses", action="store_true", help="list the questions answered below rank 3")
    args = ap.parse_args()

    real_local = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(args.appdata) if args.appdata else real_local / "UEFN-Ducky"
    if args.tools_json:
        raw = json.loads(Path(args.tools_json).read_text(encoding="utf-8"))
        catalog = list((raw.get("result") or raw).get("tools") or [])
    else:
        catalog = _live_catalog(args.port)

    # Index into a throwaway database: the person's ducky.db is never opened.
    scratch = Path(tempfile.mkdtemp(prefix="ducky-search-eval-"))
    atexit.register(_drop_scratch, scratch)
    os.environ["LOCALAPPDATA"] = str(scratch)
    sys.path.insert(0, str(ROOT / "ducky_app"))
    from backend.search import indexer, query
    from backend.agent.toolsets.tool_index import search_tool_catalog

    started = time.perf_counter()
    sources = indexer.pack_sources_from_dirs(_corpus_packs(appdata, Path(args.repos) if args.repos else None))
    tool_rows = [
        {**t, "owner_label": t.get("category_label") or "", "plugin_id": ""} for t in catalog if t.get("name")
    ]
    indexer.index_packs(sources, prune=False)
    indexer.index_tools(tool_rows, "eval")
    indexer.fill_missing_vectors()
    build_s = round(time.perf_counter() - started, 2)

    tool_names = {t["name"] for t in tool_rows}
    everything = query.Context(allowed_tools=tool_names)
    skills = _load("skills.jsonl")
    tools = _load("tools.jsonl")
    hint_items = _load("hints.jsonl")
    # A tool can be hidden in this app's state (Epic's MCP replaces Ducky's own editor tools
    # while it is online); a question whose answers are all hidden cannot be scored.
    skipped_tools = [i["q"] for i in tools if not set(i["expect"]) & {t["name"] for t in catalog}]
    tools = [i for i in tools if i["q"] not in skipped_tools]
    known_skills = {f"{s.pack_id}/{sub['id']}" for s in sources for sub in s.manifest.get("subskills") or []}
    unknown = sorted({e for item in skills for e in item["expect"]} - known_skills)
    unknown_tools = sorted({e for item in tools for e in item["expect"]} - tool_names)

    labels = {
        f"{s.pack_id}/{sub['id']}": " ".join(
            [s.pack_id, str(sub["id"]), str(s.manifest.get("label") or ""), str(sub.get("label") or "")]
        ).lower()
        for s in sources
        for sub in s.manifest.get("subskills") or []
    }

    def today_skills(q: str) -> list[str]:
        words = set(TOKEN.findall(q.lower()))
        scored = []
        for key, text in labels.items():
            hit = len(words & set(TOKEN.findall(text)))
            if hit:
                scored.append((-hit, key))
        return [k for _, k in sorted(scored)]

    def today_tools(q: str) -> list[str]:
        return [m["name"] for m in search_tool_catalog(catalog, q, limit=10)["matches"]]

    def new_skills(q: str, meaning: bool) -> list[str]:
        rows = query.search(q, limit=10, context=everything, refresh=False, use_meaning=meaning)
        return [_skill_key(r) for r in rows]

    def new_tools(q: str, meaning: bool) -> list[str]:
        rows = query.search(q, kinds=query.TOOL_KINDS, limit=10, context=everything, refresh=False, use_meaning=meaning)
        return [r["tool"] for r in rows]

    engines = ["today", "keyword", "hybrid"] if args.engine == "all" else [args.engine]
    report: dict = {"index_seconds": build_s, "unknown_expected_skills": unknown, "unknown_expected_tools": unknown_tools,
                    "skipped_tool_questions": skipped_tools}
    for engine in engines:
        if engine == "today":
            s_rank, t_rank = today_skills, today_tools
        else:
            meaning = engine == "hybrid"
            s_rank = lambda q, m=meaning: new_skills(q, m)  # noqa: E731
            t_rank = lambda q, m=meaning: new_tools(q, m)  # noqa: E731
        agent_items = [i for i in skills if i.get("style") != "user"]
        user_items = [i for i in skills if i.get("style") == "user"]
        report[engine] = {
            "skills": _score(agent_items, s_rank),
            "skills_typed_by_people": _score(user_items, s_rank),
            "tools": _score(tools, t_rank),
        }
    if "hybrid" in engines:
        report["hints"] = _hint_score(skills, hint_items, everything)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"index built in {report['index_seconds']} s from {len(sources)} packs and {len(tool_rows)} tools")
    if unknown:
        print(f"expected skills not installed here: {unknown}")
    if skipped_tools:
        print(f"{len(skipped_tools)} tool questions skipped (their tools are hidden in this app state)")
    for engine in engines:
        for name, res in report[engine].items():
            print(f"{engine:8} {name:24} n={res['n']:3} hit@1 {res['hit@1']:.3f}  hit@3 {res['hit@3']:.3f}  MRR {res['mrr']:.3f}")
            if args.misses:
                for miss in res["misses"]:
                    print(f"           miss r={miss['rank']}: {miss['q']!r} want {miss['expect'][:2]} got {miss['got'][:3]}")
    if "hints" in report:
        h = report["hints"]
        print(f"hints    requests hinted {h['requests_hinted']:.3f} (right section {h['right_section']:.3f})  "
              f"chat hinted {h['chat_hinted']:.3f}")
        if args.misses:
            for q in h["wrong"]:
                print(f"           wrong: {q!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
