"""``ducky_find``: the person's own things in every plugin, only in copies they can open.

A plugin keeps its data with ``api.data`` (``PluginData``): docs and files per scope
(Local, or a team's shared copy). Every doc already has a name people use
(``item_label``: its ``name`` / ``title`` / ``label``), so core can find "the promo"
in any plugin without the plugin writing a search tool. A plugin's own ``api.search``
records (long text: pages, cards) are searched too.

What it searches: every enabled plugin's Local copy and every team copy this account
has on this PC whose access is current. A lost team (``lost``) or a copy whose data key
isn't here (``locked``) is skipped, and ``sensitive`` docs are never read.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_WORD = re.compile(r"[^\W_]+")
# File types say what kind of file, not which one: "promo mp4" matched every recording .mp4.
_FILE_TYPES = frozenset("mp4 mov webm mkv avi wav mp3 ogg flac m4a png jpg jpeg webp gif svg pdf json txt".split())
# Docs read per kind (key prefix) per copy. Per kind, not per copy: Island Media Kit keeps
# ~10k undo and job docs that sort before ``project.`` (Oct 26 audit).
# ponytail: reads docs live; a name index per scope if a kind outgrows this.
MAX_PER_KIND = 1000
# (account, scope, plugin, key) -> (sha256, name): a doc is opened again only when it changed.
# 14k docs took ~5 s to open on every call (Oct 26); names are short, so this stays small.
_NAMES: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
# A doc's file on this PC, under the field names plugins use for it: a hit with a ``path``
# plays on the chat's tool card (videos, pictures, sounds).
_PATH_FIELDS = ("path", "local_output_path", "local_path", "file")


def _doc_path(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for field in _PATH_FIELDS:
        p = value.get(field)
        if isinstance(p, str) and os.path.isabs(p):
            return p
    return ""
_SKIP_STATES = frozenset({"lost", "locked"})


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").casefold())


def score(label: str, key: str, query: str) -> float:
    """How well a name answers the query: 3 exact, 2 contains it, else the share of query
    words that start a word of the name or key (0 when fewer than half do)."""
    q, name = " ".join(_words(query)), " ".join(_words(label))
    if not q:
        return 0.0
    if q == name:
        return 3.0
    if q in name:
        return 2.0
    have = set(_words(label)) | set(_words(key))
    qw = _words(query)
    matched = [w for w in qw if any(h.startswith(w) for h in have)]
    if not any(w not in _FILE_TYPES for w in matched):
        return 0.0
    share = len(matched) / len(qw)
    return share if share >= 0.5 else 0.0


def _copies(account: str) -> list[dict[str, Any]]:
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.scopes import PERSONAL, personal_scope, shared_scope

    out = [personal_scope(account)]
    for sid in repo.scope_ids(account):
        if sid != PERSONAL:
            out.append(shared_scope(account, sid))
    return [s for s in out if s["state"] not in _SKIP_STATES]


def _tools_of(plugin_id: str) -> list[str]:
    try:
        from backend.uefn_plugins.host import _PLUGIN_TOOL_OWNER

        return sorted(t for t, p in dict(_PLUGIN_TOOL_OWNER).items() if p == plugin_id)
    except Exception:  # noqa: BLE001
        return []


def open_with(tools: list[str], key: str) -> str:
    """The plugin tool that opens an item of this key: ``project.80a9`` -> ``imk_project_get``."""
    kind = re.split(r"[./:]", key, maxsplit=1)[0].casefold()
    if not kind:
        return ""
    for suffix in (f"_{kind}_get", f"_{kind}_list", f"{kind}_get", f"{kind}_list"):
        hit = next((t for t in tools if t.endswith(suffix)), "")
        if hit:
            return hit
    return ""


def _plugin_hits(pid: str, label: str, scope: dict[str, Any], query: str, active: bool) -> list[dict[str, Any]]:
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.data_crypto import Locked, open_text
    from backend.uefn_plugins.scopes import item_label

    hits: list[dict[str, Any]] = []
    tools = _tools_of(pid)
    copy = "Local" if scope["kind"] != "shared" else str(scope["label"])
    base = {"plugin": pid, "plugin_label": label, "copy": copy, "plugin_shows_this_copy": active}
    read: dict[str, int] = {}
    for row in repo.rows(scope["account"], scope["id"], pid, "doc"):
        if row["sensitive"] or row["value"] is None:
            continue
        kind = re.split(r"[./:]", row["key"], maxsplit=1)[0]
        read[kind] = read.get(kind, 0) + 1
        if read[kind] > MAX_PER_KIND:
            continue
        ck = (scope["account"], scope["id"], pid, row["key"])
        cached = _NAMES.get(ck)
        if cached and cached[0] == row["sha256"]:
            name, path = cached[1], cached[2]
        else:
            try:
                value = json.loads(open_text(row["value"], scope["account"]))
            except (Locked, ValueError):
                continue
            name, path = item_label(value, row["key"]), _doc_path(value)
            _NAMES[ck] = (row["sha256"], name, path)
        s = score(name, row["key"], query)
        if s:
            hit = {**base, "kind": "doc", "label": name, "key": row["key"],
                   "open_with": open_with(tools, row["key"]), "_score": s}
            if path and os.path.isfile(path):
                hit["path"] = path
            hits.append(hit)
    for row in repo.rows(scope["account"], scope["id"], pid, "asset"):
        name = row["key"].rsplit("/", 1)[-1]
        s = score(name, row["key"], query)
        if s:
            hits.append({**base, "kind": "file", "label": name, "key": row["key"], "open_with": "", "_score": s})
    return hits


def _record_hits(pid: str, label: str, query: str, limit: int) -> list[dict[str, Any]]:
    try:
        from backend.search import records

        rows = records.query(pid, query, limit)
    except Exception:  # noqa: BLE001 — no records, or the account's key isn't here
        return []
    out = []
    for r in rows:
        match = r.get("match") or {}
        if not match.get("keyword_rank") and (match.get("meaning") or 0) < 0.3:
            continue  # ranked, but not about the query
        out.append({"plugin": pid, "plugin_label": label, "copy": "", "plugin_shows_this_copy": True,
                    "kind": "record", "label": r.get("title") or r.get("id"), "key": r.get("id"),
                    "open_with": "", "snippet": r.get("snippet", ""), "_score": 1.0})
    return out


def find(query: str, *, plugin: str = "", limit: int = 10) -> dict[str, Any]:
    from backend.uefn_plugins.host import _plugin_label, is_plugin_enabled
    from backend.uefn_plugins.scopes import account_id, active_scope

    query = " ".join((query or "").split())
    if not _words(query):
        return {"ok": False, "error": "Say what to find, e.g. ducky_find(\"promo video\")."}
    limit = max(1, min(int(limit or 10), 50))
    account = account_id()
    from backend.store.repos import plugin_data as repo

    hits: list[dict[str, Any]] = []
    searched: dict[str, set[str]] = {}
    seen: set[str] = set()

    for scope in _copies(account):
        for pid in repo.plugin_ids(account, scope["id"]):
            if (plugin and pid != plugin) or not is_plugin_enabled(pid):
                continue
            label = _plugin_label(pid)
            seen.add(pid)
            try:
                showing = active_scope(pid)
                active = (showing["account"], showing["id"]) == (scope["account"], scope["id"])
                hits += _plugin_hits(pid, label, scope, query, active)
            except Exception:  # noqa: BLE001 — one plugin's bad row never hides the others
                continue
            searched.setdefault(label, set()).add("Local" if scope["kind"] != "shared" else str(scope["label"]))
    for pid in sorted(seen | ({plugin} if plugin and is_plugin_enabled(plugin) else set())):
        hits += _record_hits(pid, _plugin_label(pid), query, 5)  # its active copy only
    hits.sort(key=lambda h: (-h["_score"], h["label"].casefold()))
    out = []
    same: set[tuple[str, str, str, str]] = set()
    for h in hits:
        if len(out) >= limit:
            break
        one = (h["plugin"], h["copy"], h["label"], h["open_with"])
        if one in same:
            continue  # the same name twice in one place (an asset and its copy) reads as noise
        same.add(one)
        h.pop("_score", None)
        if not h["plugin_shows_this_copy"]:
            h["note"] = (f"{h['plugin_label']} is showing another copy, so its own tools read that one. "
                         f"Ask the person before switching it: ducky_plugin_data_scope(id=\"{h['plugin']}\", scope=…).")
        out.append(h)
    result: dict[str, Any] = {"ok": True, "query": query, "count": len(out), "hits": out,
                              "searched": {k: sorted(v) for k, v in sorted(searched.items())}}
    if not out:
        result["hint"] = ("Nothing with that name in your plugins. Try fewer or other words; never make or pay for "
                          "something the person says they already have without asking.")
    return result
