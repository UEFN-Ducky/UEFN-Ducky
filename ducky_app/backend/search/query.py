"""Ranked search over the skill and tool index: keywords + meaning, then what this chat may use.

1. Keyword candidates: FTS5 bm25 over title, heading path, body and a tool's summary
   line (top 50).
2. Meaning candidates: cosine between the query vector and every row vector (top 50).
3. Reciprocal rank fusion (k=60) merges the two lists; an exact pack / sub-skill id
   or tool name in the query is pinned first.
4. Rows this chat may not use are dropped *after* ranking (packs denied for the
   ducky, sub-skills outside its allowlist, plugins switched off, tools outside the
   chat's live catalog), so switching a plugin on shows its rows without a reindex.
5. At most two rows per sub-skill, so one long file cannot fill the page.

Falls back to keywords alone when the model is missing, and to an empty result
(callers keep their old behaviour) when the index cannot be read.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from backend.search import chunker
from backend.store.repos import search as repo

_log = logging.getLogger("uefn_ducky.search")

SKILL_KINDS = ("pack", "subskill", "section")
TOOL_KINDS = ("tool",)
CANDIDATES = 50
RRF_K = 60
# Reciprocal rank fusion weights (keyword, meaning), tuned on backend/search/eval.
WEIGHTS = {"skills": (1.0, 1.0), "tools": (1.0, 0.75)}
PER_SUBSKILL = 2
_ID_PAIR = re.compile(r"^\s*([a-z][a-z0-9_-]{0,63})\s*/\s*([a-z][a-z0-9_-]{0,63})\s*$")
_NAME = re.compile(r"[A-Za-z0-9_\-]+")

_cache_lock = threading.Lock()
_load_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None}


@dataclass
class Context:
    """What the calling chat may see. ``allowed_tools`` None = no tool rows."""

    allowed_tools: set[str] | None = None
    disabled_packs: frozenset[str] = frozenset()
    enabled_subskills: dict[str, frozenset[str]] | None = None
    plugin_enabled: Callable[[str], bool] | None = None
    record_plugin: str = ""
    record_scope: str = ""
    extra: Callable[[dict[str, Any]], bool] | None = None
    _plugin_cache: dict[str, bool] = field(default_factory=dict)

    def plugin_on(self, plugin_id: str) -> bool:
        if not plugin_id or self.plugin_enabled is None:
            return True
        if plugin_id not in self._plugin_cache:
            try:
                self._plugin_cache[plugin_id] = bool(self.plugin_enabled(plugin_id))
            except Exception:  # noqa: BLE001
                self._plugin_cache[plugin_id] = False
        return self._plugin_cache[plugin_id]

    def allows(self, doc: dict[str, Any]) -> bool:
        kind = doc.get("kind")
        if kind == "record":
            return bool(self.record_plugin) and doc.get("plugin_id") == self.record_plugin and (
                not self.record_scope or doc.get("scope") == self.record_scope
            )
        if kind == "tool":
            ok = self.allowed_tools is not None and doc.get("tool_name") in self.allowed_tools
        else:
            pack = str(doc.get("pack_id") or "")
            ok = pack not in self.disabled_packs and self.plugin_on(str(doc.get("plugin_id") or ""))
            if ok and kind in ("subskill", "section") and self.enabled_subskills is not None:
                allow = self.enabled_subskills.get(pack)
                always = bool((doc.get("meta") or {}).get("always_on"))
                ok = allow is None or always or str(doc.get("subskill_id") or "") in allow
        if ok and self.extra is not None:
            ok = bool(self.extra(doc))
        return ok


def chat_context(*, allowed_tools: set[str] | None = None) -> Context:
    """The current run's skill selection and plugin switches (see backend.skills.selection)."""
    from backend.skills.selection import active_disabled_packs, active_enabled_subskills

    try:
        from backend.uefn_plugins.host import is_plugin_enabled
    except Exception:  # noqa: BLE001
        is_plugin_enabled = None  # type: ignore[assignment]
    return Context(
        allowed_tools=allowed_tools,
        disabled_packs=active_disabled_packs(),
        enabled_subskills=active_enabled_subskills(),
        plugin_enabled=is_plugin_enabled,
    )


# --------------------------------------------------------------------------- vectors


def _matrix(model: str, account: str = "", vec_key: bytes | None = None):
    """(ids, kinds, matrix) for every vector of ``model``, cached per index revision and
    signed-in account (whose sealed record vectors it opens with ``vec_key``).

    One thread loads a new revision while the others wait for it (single flight): after
    an index change, concurrent searches used to each read every vector.
    """
    import numpy as np

    key = (model, repo.revision_token(), account if vec_key else "")
    with _cache_lock:
        if _cache.get("key") == key:
            return _cache["ids"], _cache["kinds"], _cache["matrix"]
    with _load_lock:
        with _cache_lock:
            if _cache.get("key") == key:
                return _cache["ids"], _cache["kinds"], _cache["matrix"]
        ids, kinds, blob = repo.load_vectors(model, account, vec_key)
        if ids:
            matrix = np.frombuffer(blob, dtype=np.float32).reshape(len(ids), -1)
        else:
            matrix = np.zeros((0, 1), dtype=np.float32)
        ids_arr = np.asarray(ids, dtype=np.int64)
        kinds_arr = np.asarray(kinds)
        with _cache_lock:
            _cache.update(key=key, ids=ids_arr, kinds=kinds_arr, matrix=matrix)
    return ids_arr, kinds_arr, matrix


def _meaning(
    text: str,
    kinds: Iterable[str],
    limit: int,
    *,
    only: list[int] | None = None,
    account: str = "",
    vec_key: bytes | None = None,
) -> list[tuple[int, float]]:
    """Top ``limit`` rows by cosine; ``only`` restricts candidates to these row ids first."""
    import numpy as np

    from backend.search.embedder import get_embedder

    emb = get_embedder()
    if emb is None:
        return []
    ids, doc_kinds, matrix = _matrix(emb.model_id, account, vec_key)
    if not len(ids) or matrix.shape[1] != emb.dim:
        return []
    q = emb.encode([text])[0]
    if not q.any():
        return []
    scores = matrix @ q
    wanted = list(kinds)
    if wanted:
        scores = np.where(np.isin(doc_kinds, wanted), scores, -2.0)
    if only is not None:
        scores = np.where(np.isin(ids, np.asarray(only, dtype=np.int64)), scores, -2.0)
    n = min(limit, len(scores))
    top = np.argpartition(-scores, n - 1)[:n]
    top = top[np.argsort(-scores[top])]
    return [(int(ids[i]), float(scores[i])) for i in top if scores[i] > -1.0]


# --------------------------------------------------------------------------- search


def _pins(text: str, kinds: tuple[str, ...]) -> list[int]:
    out: list[int] = []
    raw = text.strip()
    if any(k in SKILL_KINDS for k in kinds):
        m = _ID_PAIR.match(raw.lower())
        if m:
            out += repo.find_ids(kind="subskill", pack_id=m.group(1), subskill_id=m.group(2))
        elif re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", raw.lower()):
            out += repo.find_ids(kind="pack", pack_id=raw.lower())
    if "tool" in kinds:
        for name in dict.fromkeys(_NAME.findall(raw)):
            if "_" in name or name == raw:
                out += repo.find_ids(kind="tool", tool_name=name)
        # A request that spells a tool's name in plain words ("run a workflow" -> run_workflow).
        spelled = repo.title_words(raw)
        if " " in spelled:
            same = repo.find_ids(kind="tool", title=spelled)
            if len(same) <= 3:
                out += same
    return list(dict.fromkeys(out))


def read_call(doc: dict[str, Any]) -> str:
    kind = doc.get("kind")
    pack, sub = doc.get("pack_id") or "", doc.get("subskill_id") or ""
    if kind == "tool":
        return f'ducky_get_tools(name="{doc.get("tool_name")}")'
    # Named, as an MCP call takes them: from a positional form agents guess the names.
    if kind == "pack":
        return f'skill_read_subskill(pack_id="{pack}", subskill_id="core")'
    if kind == "section" and doc.get("section"):
        section = str(doc["section"]).replace('"', "'")
        return f'skill_read_subskill(pack_id="{pack}", subskill_id="{sub}", section="{section}")'
    if kind in ("subskill", "section"):
        return f'skill_read_subskill(pack_id="{pack}", subskill_id="{sub}")'
    return ""


def _result(doc: dict[str, Any], score: float, kw_rank: int | None, meaning: float | None) -> dict[str, Any]:
    meta = doc.get("meta") or {}
    kind = doc.get("kind")
    if kind == "tool":
        text = str(meta.get("description") or "")
    elif kind == "subskill":
        text = str(meta.get("description") or "") or chunker.snippet(doc.get("body") or "")
    else:
        text = chunker.snippet(doc.get("body") or "")
    row: dict[str, Any] = {
        "kind": kind,
        "title": doc.get("title") or "",
        "where": doc.get("path") or "",
        "snippet": chunker.snippet(text),
        "score": round(score, 5),
    }
    if kind == "tool":
        row["tool"] = doc.get("tool_name")
    elif kind == "record":
        # The id comes from the key (record ids have no ':'), never from the plugin's meta.
        rid = str(doc.get("doc_key") or "").rsplit(":", 1)[-1]
        row["id"] = rid
        row["meta"] = {k: v for k, v in meta.items() if not (k == "id" and v == rid)}
    else:
        row["pack"] = doc.get("pack_id")
        if kind != "pack":
            row["subskill"] = doc.get("subskill_id")
        if kind == "section":
            row["section"] = doc.get("section")
    if doc.get("plugin_id"):
        row["plugin"] = doc["plugin_id"]
    call = read_call(doc)
    if call:
        row["read"] = call
    row["match"] = {"keyword_rank": kw_rank, "meaning": None if meaning is None else round(meaning, 4)}
    return row


def search(
    text: str,
    *,
    kinds: Iterable[str] = SKILL_KINDS,
    limit: int = 8,
    context: Context | None = None,
    refresh: bool = True,
    use_keywords: bool = True,
    use_meaning: bool = True,
) -> list[dict[str, Any]]:
    """Ranked rows for ``text`` that ``context`` allows; [] when nothing matches."""
    text = (text or "").strip()
    kinds = tuple(k for k in kinds if k in repo.KINDS) or SKILL_KINDS
    limit = max(1, min(int(limit or 8), 50))
    if not text:
        return []
    ctx = context or chat_context()
    if refresh:
        try:
            from backend.search import indexer

            indexer.maybe_refresh(wait_s=0.0 if repo.has_docs() else 2.0)
        except Exception:  # noqa: BLE001
            _log.debug("search: refresh check failed", exc_info=True)
    # A plugin's records compete only with its own rows in its own scope, before the cut:
    # filtered afterwards, other plugins' better matches could fill every candidate slot.
    source = ""
    if kinds == ("record",) and ctx.record_plugin and ctx.record_scope:
        source = f"record:{ctx.record_plugin}:{ctx.record_scope}"
    from backend.store import account_rows, db

    # Plugin records are sealed for their account: its key is loaded before the read
    # snapshot below (loading may write it), and their keywords match blinded words.
    me = account_rows.current()
    try:
        # Every search, records or not: they share one cached vector matrix per account.
        vec_key: bytes | None = account_rows.ready(me)
    except Exception:  # noqa: BLE001 — locked: sealed records stay out of this search
        vec_key = None
    match: str | None = None
    if source and vec_key:
        from backend.search import sealed_records

        plain, blind = repo.fts_query(text, prefix=False), sealed_records.blind_match(text, vec_key)
        # Rows from before sealing still match on their words until the seal pass reaches
        # them; exact words only, since a prefix would also match blinded tokens.
        match = " OR ".join(f"({q})" for q in (plain, blind) if q)
    conn = db.connect()
    # One read transaction is one WAL snapshot: the keyword ids, the vector ids, the
    # pins and the rows they name all come from the same state of the index. Read
    # separately, a rebuild in between re-used ids for unrelated rows.
    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        try:
            keyword = repo.keyword(text, kinds, CANDIDATES, source_key=source, match=match) if use_keywords else []
        except Exception:  # noqa: BLE001
            _log.debug("search: keyword query failed", exc_info=True)
            return []
        try:
            only = repo.source_ids(source) if source else None
            meaning = (
                _meaning(text, kinds, CANDIDATES, only=only, account=me, vec_key=vec_key) if use_meaning else []
            )
        except Exception:  # noqa: BLE001
            _log.debug("search: meaning query failed", exc_info=True)
            meaning = []
        fused: dict[int, float] = {}
        kw_rank: dict[int, int] = {}
        cos: dict[int, float] = {}
        w_kw, w_mean = WEIGHTS["tools" if kinds == TOOL_KINDS else "skills"]
        for rank, (doc_id, _) in enumerate(keyword):
            fused[doc_id] = fused.get(doc_id, 0.0) + w_kw / (RRF_K + rank + 1)
            kw_rank[doc_id] = rank + 1
        for rank, (doc_id, score) in enumerate(meaning):
            fused[doc_id] = fused.get(doc_id, 0.0) + w_mean / (RRF_K + rank + 1)
            cos[doc_id] = score
        pins = _pins(text, kinds)
        top = 1.0 + max(fused.values(), default=0.0)
        for i, doc_id in enumerate(pins):
            fused[doc_id] = top + (len(pins) - i)
        ordered = sorted(fused, key=lambda d: -fused[d])[: max(limit * 6, 60)]
        docs = repo.get_docs(ordered, account=me)
    finally:
        if own_snapshot:
            try:
                conn.execute("COMMIT")
            except Exception:  # noqa: BLE001
                _log.debug("search: closing the read snapshot failed", exc_info=True)
    out: list[dict[str, Any]] = []
    per_sub: dict[tuple[str, str], int] = {}
    reads: set[str] = set()
    for doc_id in ordered:
        doc = docs.get(doc_id)
        if doc is None or doc.get("kind") not in kinds or not ctx.allows(doc):
            continue
        if doc["kind"] in ("subskill", "section"):
            key = (str(doc.get("pack_id")), str(doc.get("subskill_id")))
            if per_sub.get(key, 0) >= PER_SUBSKILL:
                continue
            per_sub[key] = per_sub.get(key, 0) + 1
        row = _result(doc, fused[doc_id], kw_rank.get(doc_id), cos.get(doc_id))
        # A pack and its core sub-skill open with the same call: list it once.
        if row.get("read") and row["read"] in reads:
            continue
        reads.add(str(row.get("read") or ""))
        out.append(row)
        if len(out) >= limit:
            break
    return out


def search_tools(text: str, allowed: set[str], *, limit: int = 20) -> list[dict[str, Any]]:
    """Tool rows only, limited to ``allowed`` (the caller's live, filtered catalog)."""
    ctx = chat_context(allowed_tools=set(allowed))
    return search(text, kinds=TOOL_KINDS, limit=limit, context=ctx)


def reset_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
        _cache["key"] = None
