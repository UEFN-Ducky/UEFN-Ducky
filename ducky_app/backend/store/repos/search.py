"""``search_docs`` + ``search_fts`` + ``search_vecs``: the skill and tool search index.

Skill, sub-skill, section and tool rows are derived data (``backend.search.indexer``
rebuilds them), so every write here is an idempotent upsert keyed by ``doc_key``: two
processes indexing the same source at once end with the same rows. Plugin records
(``api.search``) have no other source: a rebuild keeps them and re-embeds them.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from backend.store import db

KINDS = ("pack", "subskill", "section", "tool", "record")
# bm25 column weights: title, heading path, body, summary (a tool's first description line,
# tuned on backend/search/eval; skill and record rows leave it empty).
_BM25 = "bm25(search_fts, 3.0, 2.0, 1.0, 2.5)"
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_QUERY_TOKENS = 16
# Words that match nearly every row; OR-ing them in only adds noise to the candidates.
_STOP = frozenset(
    "a an and are as at be by can do does for from how i in into is it its me my of on or our so that the "
    "their them then there these this to up we what when where which why will with you your".split()
)


@dataclass
class Doc:
    doc_key: str
    source_key: str
    kind: str
    title: str
    body: str
    path: str = ""
    summary: str = ""
    origin: str = ""
    plugin_id: str = ""
    pack_id: str = ""
    subskill_id: str = ""
    section: str = ""
    tool_name: str = ""
    scope: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def row(self) -> tuple[Any, ...]:
        return (
            self.doc_key, self.source_key, self.kind, self.origin, self.plugin_id, self.pack_id,
            self.subskill_id, self.section, self.tool_name, self.scope, self.title, self.path,
            self.body, self.summary, json.dumps(self.meta, sort_keys=True, ensure_ascii=False), self.content_hash,
            time.time(),
        )


_COLUMNS = (
    "doc_key, source_key, kind, origin, plugin_id, pack_id, subskill_id, section, tool_name, "
    "scope, title, path, body, summary, meta, content_hash, updated"
)


def existing(source_key: str, model: str) -> dict[str, tuple[int, str, bool]]:
    """doc_key -> (id, content_hash, has a vector for ``model``) for one source."""
    rows = db.connect().execute(
        "SELECT d.id, d.doc_key, d.content_hash, (v.doc_id IS NOT NULL) AS has_vec FROM search_docs d "
        "LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ? WHERE d.source_key = ?",
        (model, source_key),
    ).fetchall()
    return {str(r["doc_key"]): (int(r["id"]), str(r["content_hash"]), bool(r["has_vec"])) for r in rows}


def write_source(
    source_key: str,
    docs: list[Doc],
    vectors: dict[str, bytes],
    *,
    model: str,
    revision: str,
    replace: bool = True,
    keep: set[str] | None = None,
) -> dict[str, int]:
    """Upsert ``docs`` and their ``vectors`` (doc_key -> float32 bytes) in one transaction.

    With ``replace`` the source's rows that are not in ``docs`` (or ``keep``) are
    deleted (a pack lost a file, a tool left the catalog). Unchanged rows are not
    rewritten.
    """
    conn = db.connect()
    added = changed = removed = 0
    keep_extra = set(keep or ())
    with db.write_txn(conn):
        have = {
            str(r["doc_key"]): (int(r["id"]), str(r["content_hash"]))
            for r in conn.execute("SELECT id, doc_key, content_hash FROM search_docs WHERE source_key=?", (source_key,))
        }
        keep: set[str] = set()
        for doc in docs:
            keep.add(doc.doc_key)
            current = have.get(doc.doc_key)
            if current is None:
                # A doc_key can move between sources (a pack moved from AppData to a plugin).
                conn.execute("DELETE FROM search_docs WHERE doc_key=?", (doc.doc_key,))
                cur = conn.execute(
                    f"INSERT INTO search_docs({_COLUMNS}) VALUES ({', '.join('?' * 17)})", doc.row()
                )
                doc_id = int(cur.lastrowid)
                added += 1
            else:
                doc_id = current[0]
                if current[1] != doc.content_hash:
                    conn.execute(
                        "UPDATE search_docs SET source_key=?, kind=?, origin=?, plugin_id=?, pack_id=?, subskill_id=?, "
                        "section=?, tool_name=?, scope=?, title=?, path=?, body=?, summary=?, meta=?, content_hash=?, updated=? "
                        "WHERE id=?",
                        (*doc.row()[1:], doc_id),
                    )
                    conn.execute("DELETE FROM search_vecs WHERE doc_id=?", (doc_id,))
                    changed += 1
            vec = vectors.get(doc.doc_key)
            if vec is not None:
                conn.execute(
                    "INSERT INTO search_vecs(doc_id, model, vec) VALUES (?, ?, ?) "
                    "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, vec=excluded.vec",
                    (doc_id, model, vec),
                )
        if replace:
            keep |= keep_extra
            for key, (doc_id, _) in have.items():
                if key not in keep:
                    conn.execute("DELETE FROM search_docs WHERE id=?", (doc_id,))
                    removed += 1
        conn.execute(
            "INSERT INTO search_state(source_key, revision, indexed) VALUES (?, ?, ?) "
            "ON CONFLICT(source_key) DO UPDATE SET revision=excluded.revision, indexed=excluded.indexed",
            (source_key, revision, time.time()),
        )
    return {"added": added, "changed": changed, "removed": removed}


def missing_vectors(model: str, limit: int = 5000) -> list[Doc]:
    """Rows with no vector for ``model`` (a model change). Sealed plugin records are left
    out: their text is sealed, ``sealed_records.fill_missing_vectors`` embeds those."""
    from backend.search.sealed_records import SEALED_PREFIX

    rows = db.connect().execute(
        "SELECT d.* FROM search_docs d LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ? "
        f"WHERE v.doc_id IS NULL AND NOT (d.kind = 'record' AND substr(d.meta, 1, {len(SEALED_PREFIX)}) = ?) LIMIT ?",
        (model, SEALED_PREFIX, max(1, int(limit))),
    ).fetchall()
    out: list[Doc] = []
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except ValueError:
            meta = {}
        out.append(Doc(
            doc_key=r["doc_key"], source_key=r["source_key"], kind=r["kind"], title=r["title"], body=r["body"],
            path=r["path"], summary=r["summary"], origin=r["origin"], plugin_id=r["plugin_id"], pack_id=r["pack_id"],
            subskill_id=r["subskill_id"], section=r["section"], tool_name=r["tool_name"], scope=r["scope"],
            meta=meta, content_hash=r["content_hash"],
        ))
    return out


def set_vectors(vectors: dict[str, bytes], model: str, hashes: dict[str, str] | None = None) -> int:
    """doc_key -> vector for rows that already exist.

    With ``hashes`` (doc_key -> the content_hash the vector was made from) a row whose
    text changed while the vector was being made is skipped: its writer stored its own
    vector, or the next fill makes one. Without the check a fill could leave a row with
    its old text's vector for good.
    """
    if not vectors:
        return 0
    conn = db.connect()
    n = 0
    with db.write_txn(conn):
        for key, vec in vectors.items():
            row = conn.execute("SELECT id, content_hash FROM search_docs WHERE doc_key=?", (key,)).fetchone()
            if row is None or (hashes is not None and str(row[1]) != hashes.get(key)):
                continue
            conn.execute(
                "INSERT INTO search_vecs(doc_id, model, vec) VALUES (?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, vec=excluded.vec",
                (int(row[0]), model, vec),
            )
            n += 1
    return n


def tool_owners(source_key: str) -> dict[str, str]:
    """doc_key -> owning desktop plugin id ('' for core and MCP tools) of one source."""
    rows = db.connect().execute(
        "SELECT doc_key, plugin_id FROM search_docs WHERE source_key=?", (source_key,)
    ).fetchall()
    return {str(r[0]): str(r[1] or "") for r in rows}


def record_state(doc_key: str, model: str) -> tuple[str, bool] | None:
    """(content_hash, has a vector for ``model``) of one row, or None when it does not exist."""
    row = db.connect().execute(
        "SELECT d.content_hash, (v.doc_id IS NOT NULL) FROM search_docs d "
        "LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ? WHERE d.doc_key = ?",
        (model, doc_key),
    ).fetchone()
    return None if row is None else (str(row[0]), bool(row[1]))


def _note_plain_records(conn: Any, where: str, args: tuple[Any, ...]) -> None:
    """Inside a write: plaintext plugin records (from before they were sealed) are about to
    be replaced or deleted. Their old words stay in the file's free space and in the
    keyword index until the one rebuild after sealing, so ask for it (``account_rows``)."""
    from backend.search.sealed_records import SEALED_PREFIX

    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value, updated) SELECT 'vacuum_wanted', '1', ? WHERE EXISTS ("
        f"SELECT 1 FROM search_docs WHERE ({where}) AND kind='record' "
        f"AND substr(meta, 1, {len(SEALED_PREFIX)}) <> ?)",
        (time.time(), *args, SEALED_PREFIX),
    )


def upsert_one(doc: Doc, vec: bytes | None, model: str, *, max_in_source: int | None = None) -> bool:
    """One record row (api.search): insert or update in place, vector alongside.

    Returns False when the row already holds this text (and a vector for ``model``).
    ``max_in_source`` caps a new row's source inside the write transaction, so writers
    in several threads or processes cannot pass the cap together.
    """
    conn = db.connect()
    with db.write_txn(conn):
        if doc.kind == "record":
            _refuse_gone_plugin(conn, doc.plugin_id)
        row = conn.execute(
            "SELECT d.id, d.content_hash, (v.doc_id IS NOT NULL) FROM search_docs d "
            "LEFT JOIN search_vecs v ON v.doc_id = d.id AND v.model = ? WHERE d.doc_key = ?",
            (model, doc.doc_key),
        ).fetchone()
        if row is None:
            if max_in_source is not None:
                n = conn.execute("SELECT count(*) FROM search_docs WHERE source_key=?", (doc.source_key,)).fetchone()[0]
                if int(n) >= max_in_source:
                    raise ValueError(f"search holds at most {max_in_source} records per plugin; delete some first")
            cur = conn.execute(f"INSERT INTO search_docs({_COLUMNS}) VALUES ({', '.join('?' * 17)})", doc.row())
            doc_id = int(cur.lastrowid)
        else:
            doc_id = int(row[0])
            if str(row[1]) == doc.content_hash and (bool(row[2]) or vec is None):
                return False
            if str(row[1]) != doc.content_hash:
                _note_plain_records(conn, "id=?", (doc_id,))
                conn.execute(
                    "UPDATE search_docs SET source_key=?, kind=?, origin=?, plugin_id=?, pack_id=?, subskill_id=?, "
                    "section=?, tool_name=?, scope=?, title=?, path=?, body=?, summary=?, meta=?, content_hash=?, "
                    "updated=? WHERE id=?",
                    (*doc.row()[1:], doc_id),
                )
                conn.execute("DELETE FROM search_vecs WHERE doc_id=?", (doc_id,))
        if vec is not None:
            conn.execute(
                "INSERT INTO search_vecs(doc_id, model, vec) VALUES (?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, vec=excluded.vec",
                (doc_id, model, vec),
            )
    return True


def find_doc(doc_key: str) -> int | None:
    row = db.connect().execute("SELECT id FROM search_docs WHERE doc_key=?", (doc_key,)).fetchone()
    return None if row is None else int(row[0])


def count_source(source_key: str) -> int:
    row = db.connect().execute("SELECT count(*) FROM search_docs WHERE source_key=?", (source_key,)).fetchone()
    return int(row[0])


def delete_docs(doc_keys: Iterable[str]) -> int:
    keys = list(doc_keys)
    if not keys:
        return 0
    conn = db.connect()
    n = 0
    with db.write_txn(conn):
        for key in keys:
            _note_plain_records(conn, "doc_key=?", (key,))
            n += conn.execute("DELETE FROM search_docs WHERE doc_key=?", (key,)).rowcount
    return n


def drop_source(source_key: str) -> int:
    conn = db.connect()
    with db.write_txn(conn):
        _note_plain_records(conn, "source_key=?", (source_key,))
        n = conn.execute("DELETE FROM search_docs WHERE source_key=?", (source_key,)).rowcount
        conn.execute("DELETE FROM search_state WHERE source_key=?", (source_key,))
    return n


def drop_sources_like(prefix: str) -> int:
    """Delete every source whose key starts with ``prefix`` (a plugin's records on uninstall)."""
    conn = db.connect()
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with db.write_txn(conn):
        _note_plain_records(conn, "source_key LIKE ? ESCAPE '\\'", (like,))
        n = conn.execute("DELETE FROM search_docs WHERE source_key LIKE ? ESCAPE '\\'", (like,)).rowcount
        conn.execute("DELETE FROM search_state WHERE source_key LIKE ? ESCAPE '\\'", (like,))
    return n


def forget_records(plugin_id: str) -> int:
    """Uninstall: delete a plugin's records and mark it gone, in one transaction.

    A record write checks the mark inside its own write transaction, so a plugin thread
    that outlives the uninstall, or the plugin's copy in an IDE bridge, cannot add rows
    back. Installing the plugin again clears the mark (:func:`clear_plugin_gone`).
    """
    like = f"record:{plugin_id}:".replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO search_meta(key, value) VALUES (?, 1) ON CONFLICT(key) DO UPDATE SET value=1",
            (f"gone:{plugin_id}",),
        )
        _note_plain_records(conn, "source_key LIKE ? ESCAPE '\\'", (like,))
        n = conn.execute("DELETE FROM search_docs WHERE source_key LIKE ? ESCAPE '\\'", (like,)).rowcount
        conn.execute("DELETE FROM search_state WHERE source_key LIKE ? ESCAPE '\\'", (like,))
    return n


def clear_plugin_gone(plugin_id: str) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM search_meta WHERE key=?", (f"gone:{plugin_id}",))


class PluginGone(ValueError):
    """A record write from a plugin whose uninstall marked it gone."""


def _refuse_gone_plugin(conn: Any, plugin_id: str) -> None:
    if plugin_id and conn.execute("SELECT 1 FROM search_meta WHERE key=?", (f"gone:{plugin_id}",)).fetchone():
        raise PluginGone(f"plugin {plugin_id} was uninstalled; its search records are gone")


def drop_scope_records(account: str, scope_id: str) -> int:
    """Delete every plugin record of one account's data scope (a shared copy purged, a
    plugin-test sandbox ended). ``scope`` of a record row is ``<account>~<scope id>``."""
    conn = db.connect()
    scope = f"{account or '_local'}~{scope_id}"
    with db.write_txn(conn):
        _note_plain_records(conn, "scope=?", (scope,))
        n = conn.execute("DELETE FROM search_docs WHERE kind='record' AND scope=?", (scope,)).rowcount
    return n


def drop_stale_sandbox_records(older_than_s: float) -> int:
    """Delete plugin-test sandbox records not written for ``older_than_s`` seconds."""
    conn = db.connect()
    where = "kind='record' AND scope LIKE ? ESCAPE '\\' AND updated < ?"
    args = ("%~\\_plugin\\_test\\_%", time.time() - older_than_s)
    if conn.execute(f"SELECT 1 FROM search_docs WHERE {where} LIMIT 1", args).fetchone() is None:
        return 0  # the common case: no write lock taken
    with db.write_txn(conn):
        _note_plain_records(conn, where, args)
        n = conn.execute(f"DELETE FROM search_docs WHERE {where}", args).rowcount
    return n


def source_keys() -> list[str]:
    rows = db.connect().execute("SELECT DISTINCT source_key FROM search_docs").fetchall()
    return [str(r[0]) for r in rows]


def source_ids(source_key: str) -> list[int]:
    """Row ids of one source (index-backed; a plugin's records are at most MAX_RECORDS)."""
    rows = db.connect().execute("SELECT id FROM search_docs WHERE source_key=?", (source_key,)).fetchall()
    return [int(r[0]) for r in rows]


def tool_names() -> set[str]:
    """Every indexed tool name (the search boxes' allowed set; no row bodies read)."""
    rows = db.connect().execute(
        "SELECT DISTINCT tool_name FROM search_docs WHERE kind='tool' AND tool_name != ''"
    ).fetchall()
    return {str(r[0]) for r in rows}


def get_state(source_key: str) -> str | None:
    row = db.connect().execute("SELECT revision FROM search_state WHERE source_key=?", (source_key,)).fetchone()
    return None if row is None else str(row[0])


def states() -> dict[str, tuple[str, float]]:
    return {
        str(r[0]): (str(r[1]), float(r[2]))
        for r in db.connect().execute("SELECT source_key, revision, indexed FROM search_state").fetchall()
    }


def wipe() -> None:
    """Drop every derived row (Rebuild, the Database tab's clear) and rebuild the keyword index.

    Plugin records have no source to rebuild them from, so they stay; their vectors go
    and the fill pass re-embeds them.
    """
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM search_vecs WHERE doc_id IN (SELECT id FROM search_docs WHERE kind='record')")
        conn.execute("DELETE FROM search_docs WHERE kind != 'record'")
        conn.execute("DELETE FROM search_state")
        conn.execute("INSERT INTO search_fts(search_fts) VALUES ('rebuild')")


# --------------------------------------------------------------------------- reads


def fts_query(text: str, *, prefix: bool = True) -> str:
    """Safe FTS5 MATCH text: quoted tokens OR'd together, the last one as a prefix
    (``prefix=False``: exact words only, for rows whose words are blinded tokens, where a
    prefix would match unrelated tokens).

    Quoting every token keeps FTS5 syntax (AND, NEAR, ``*``, ``:``, quotes) in the
    person's words from being parsed as operators.
    """
    tokens = [t.lower() for t in _TOKEN.findall(text or "")]
    tokens = list(dict.fromkeys([t for t in tokens if t not in _STOP] or tokens))[:_MAX_QUERY_TOKENS]
    if not tokens:
        return ""
    parts = [f'"{t}"' for t in tokens[:-1]]
    last = tokens[-1]
    parts.append(f'"{last}"*' if prefix and len(last) >= 3 else f'"{last}"')
    return " OR ".join(parts)


def keyword(
    text: str, kinds: Iterable[str] | None = None, limit: int = 50, *, source_key: str = "", match: str | None = None
) -> list[tuple[int, float]]:
    """(doc id, bm25 score — lower is better) for the best keyword matches.

    ``source_key`` limits candidates to one source before the cut (a plugin's records in
    one scope must not be crowded out by other plugins' rows). ``match`` is a ready FTS5
    query in place of ``text``'s (sealed records match on blinded words)."""
    match = fts_query(text) if match is None else match
    if not match:
        return []
    sql = (
        f"SELECT d.id, {_BM25} AS score FROM search_fts JOIN search_docs d ON d.id = search_fts.rowid "
        "WHERE search_fts MATCH ?"
    )
    args: list[Any] = [match]
    kinds = [k for k in (kinds or []) if k in KINDS]
    if kinds:
        sql += f" AND d.kind IN ({', '.join('?' * len(kinds))})"
        args.extend(kinds)
    if source_key:
        sql += " AND d.source_key = ?"
        args.append(source_key)
    sql += " ORDER BY score LIMIT ?"
    args.append(max(1, int(limit)))
    try:
        rows = db.connect().execute(sql, args).fetchall()
    except db.DatabaseError:
        return []
    return [(int(r[0]), float(r[1])) for r in rows]


def load_vectors(model: str, account: str = "", key: bytes | None = None) -> tuple[list[int], list[str], bytes]:
    """Every vector of ``model``: (doc ids, kinds, concatenated float32 bytes) in id order.
    Sealed record vectors are opened for ``account`` (the signed-in one) with its chat
    ``key``; other accounts' (and all of them without a key) are left out."""
    from backend.search import sealed_records

    rows = db.connect().execute(
        "SELECT v.doc_id, d.kind, d.scope, v.vec FROM search_vecs v JOIN search_docs d ON d.id = v.doc_id "
        "WHERE v.model = ? ORDER BY v.doc_id",
        (model,),
    ).fetchall()
    ids: list[int] = []
    kinds: list[str] = []
    vecs: list[bytes] = []
    for doc_id, kind, scope, raw in rows:
        vec = bytes(raw)
        if vec.startswith(sealed_records.VEC_MAGIC):
            if not key or not account or sealed_records.owner(str(scope or "")) != account:
                continue
            try:
                vec = sealed_records.open_vec(vec, key)
            except Exception:  # noqa: BLE001 — sealed with a key restored away; re-embedded on its next write
                continue
        ids.append(int(doc_id))
        kinds.append(str(kind))
        vecs.append(vec)
    return ids, kinds, b"".join(vecs)


def get_docs(ids: Iterable[int], account: str | None = None) -> dict[int, dict[str, Any]]:
    """Rows by id. A sealed plugin record is opened when it is ``account``'s (the
    signed-in one by default) and comes back empty otherwise."""
    from backend.search import sealed_records

    wanted = list(dict.fromkeys(int(i) for i in ids))
    out: dict[int, dict[str, Any]] = {}
    conn = db.connect()
    me: str | None = account
    for start in range(0, len(wanted), 500):
        chunk = wanted[start : start + 500]
        rows = conn.execute(
            f"SELECT id, doc_key, source_key, kind, origin, plugin_id, pack_id, subskill_id, section, tool_name, scope, "
            f"title, path, body, meta FROM search_docs WHERE id IN ({', '.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for r in rows:
            row = dict(r)
            raw_meta = row.get("meta") or "{}"
            try:
                row["meta"] = json.loads(raw_meta)
            except ValueError:
                row["meta"] = {}
            if row.get("kind") == "record" and sealed_records.is_sealed_meta(raw_meta):
                if me is None:
                    from backend.store import account_rows

                    me = account_rows.current()
                try:
                    row = sealed_records.open_row(row, me)
                except Exception:  # noqa: BLE001 — locked for now: shown empty, never as ciphertext
                    row = {**row, "title": "", "path": "", "body": "", "meta": {}}
            out[int(r["id"])] = row
    return out


def title_words(text: str) -> str:
    """The words of ``text`` without stop words, lower case: "run a workflow" -> "run workflow"."""
    return " ".join(t for t in _TOKEN.findall(text.lower()) if t not in _STOP)


def find_ids(
    *, kind: str, pack_id: str = "", subskill_id: str = "", tool_name: str = "", title: str = ""
) -> list[int]:
    """Exact lookups for pinning: a pack or sub-skill id, a tool name, or a title (case-insensitive)."""
    sql = "SELECT id FROM search_docs WHERE kind=?"
    args: list[Any] = [kind]
    if title:
        sql += " AND lower(title)=?"
        args.append(title.lower())
    if pack_id:
        sql += " AND pack_id=?"
        args.append(pack_id)
    if subskill_id:
        sql += " AND subskill_id=?"
        args.append(subskill_id)
    if tool_name:
        sql += " AND tool_name=?"
        args.append(tool_name)
    return [int(r[0]) for r in db.connect().execute(sql, args).fetchall()]


def has_docs() -> bool:
    return db.connect().execute("SELECT 1 FROM search_docs LIMIT 1").fetchone() is not None


def counts() -> dict[str, Any]:
    conn = db.connect()
    kinds = {str(r[0]): int(r[1]) for r in conn.execute("SELECT kind, count(*) FROM search_docs GROUP BY kind")}
    vecs = {str(r[0]): int(r[1]) for r in conn.execute("SELECT model, count(*) FROM search_vecs GROUP BY model")}
    last = conn.execute("SELECT max(indexed) FROM search_state").fetchone()[0]
    return {"docs": kinds, "vectors": vecs, "last_indexed": float(last or 0), "sources": len(states())}


def data_version() -> int:
    return db.data_version(db.connect())


def revision_token() -> str:
    """Changes on every insert, update or delete of an index row or vector, by any
    process (triggers count them in search_meta, migration 0018). One row read; it
    keys the per-process vector-matrix cache."""
    row = db.connect().execute("SELECT value FROM search_meta WHERE key='writes'").fetchone()
    return str(row[0] if row else 0)
