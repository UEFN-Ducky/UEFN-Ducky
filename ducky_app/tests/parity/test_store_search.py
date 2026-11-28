"""The search repo: the skill, tool and plugin-record index with its vectors."""

from __future__ import annotations

import base64
import dataclasses

import pytest

from backend.store.repos import search

from .parity_harness import plain
from .test_store_chats import _sql

V1, V2, V3 = bytes(range(8)), bytes(range(8, 16)), b"\x00\xff" * 4


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode()


def _doc(key: str, source: str = "skills:a", kind: str = "section", **fields) -> search.Doc:
    doc = search.Doc(
        doc_key=key, source_key=source, kind=kind, title=f"Title {key}", body=f"body words for {key} é", path="A > B",
        origin="builtin", meta={"z": 1, "a": {"y": [1.5, None], "b": "é"}}, content_hash=f"h-{key}",
    )
    return dataclasses.replace(doc, **fields)


def _record(key: str, plugin: str = "plug", scope: str = "acct~s1", **fields) -> search.Doc:
    return _doc(key, f"record:{plugin}:{scope}", "record", plugin_id=plugin, scope=scope, **fields)


class _Rust:
    """The Rust repo, called as Python's module is; a refusal raises as Python's does."""

    def __init__(self, backend) -> None:
        self.backend = backend

    def _call(self, function: str, **args):
        status, reply = self.backend.request(f"/__rs/store/search/{function}", args)
        assert status == 200, f"search.{function}: {status} {reply}"
        if not reply["ok"]:
            raise ValueError(reply["error"])
        return reply["result"]

    def __getattr__(self, function: str):
        return lambda **args: self._call(function, **args)

    def write_source(self, source_key, docs, vectors, *, model, revision, replace=True, keep=None):
        return self._call(
            "write_source", source_key=source_key, docs=[dataclasses.asdict(doc) for doc in docs],
            vectors={key: _b64(vec) for key, vec in vectors.items()}, model=model, revision=revision, replace=replace,
            keep=sorted(keep or ()),
        )

    def set_vectors(self, vectors, model, hashes=None):
        return self._call("set_vectors", vectors={key: _b64(vec) for key, vec in vectors.items()}, model=model, hashes=hashes)

    def upsert_one(self, doc, vec, model, *, max_in_source=None):
        return self._call(
            "upsert_one", doc=dataclasses.asdict(doc), vec=None if vec is None else _b64(vec), model=model, max_in_source=max_in_source
        )


def _snapshot() -> dict:
    return {
        "docs": sorted(_sql(
            "SELECT d.doc_key, d.source_key, d.kind, d.origin, d.plugin_id, d.pack_id, d.subskill_id, d.section, d.tool_name, "
            "d.scope, d.title, d.path, d.body, d.summary, d.meta, d.content_hash, v.model, v.vec "
            "FROM search_docs d LEFT JOIN search_vecs v ON v.doc_id = d.id"
        )),
        "state": sorted(_sql("SELECT source_key, revision FROM search_state")),
        "gone": sorted(_sql("SELECT key, value FROM search_meta WHERE key LIKE 'gone:%'")),
        "vacuum": _sql("SELECT value FROM meta WHERE key='vacuum_wanted'"),
        "matching": sorted(_sql("SELECT d.doc_key FROM search_fts JOIN search_docs d ON d.id = search_fts.rowid WHERE search_fts MATCH 'body'")),
    }


def _clear() -> None:
    for statement in (
        "DELETE FROM search_vecs", "DELETE FROM search_docs", "DELETE FROM search_state",
        "DELETE FROM search_meta WHERE key LIKE 'gone:%'", "DELETE FROM meta WHERE key='vacuum_wanted'",
    ):
        _sql(statement)


def _steps(api) -> list:
    """The same writes through either backend: what each answered, the rows it left and
    how many row writes it took."""
    started = int(search.revision_token())
    out = []

    def step(call) -> None:
        try:
            answer = call()
        except ValueError as err:
            answer = f"refused: {err}"
        out.append((len(out), plain(answer), _snapshot(), int(search.revision_token()) - started))

    tools = [
        _doc("t1", "tools", "tool", tool_name="spawn_actor", plugin_id="plug", title="Spawn Actor"),
        _doc("t2", "tools", "tool", tool_name="run_workflow", title="Run a Workflow"),
    ]
    step(lambda: api.write_source("skills:a", [_doc("a1"), _doc("a2"), _doc("a3")], {"a1": V1, "a3": V2}, model="m1", revision="r1"))
    step(lambda: api.write_source("tools", tools, {"t1": V1}, model="m1", revision="t1"))
    # a1 as it was, a2 changed, a3 gone but kept, a4 new, t2 moves in from another source.
    again = [_doc("a1"), _doc("a2", content_hash="h2", body="changed body"), _doc("a4", meta={}), _doc("t2", "skills:a", "pack", pack_id="pk")]
    step(lambda: api.write_source("skills:a", again, {"a4": V3, "a1": V3}, model="m2", revision="r2", keep={"a3"}))
    step(lambda: api.write_source("skills:a", again, {}, model="m2", revision="r2"))  # nothing to do; a3 goes
    step(lambda: api.write_source("skills:b", [_doc("b1", "skills:b"), _doc("b2", "skills:b")], {}, model="m1", revision="b1", replace=False))
    step(lambda: api.write_source("skills:b", [_doc("b1", "skills:b", content_hash="new")], {}, model="m1", revision="b2", replace=False))
    step(lambda: api.set_vectors({"a2": V1, "none": V2, "a4": V2}, "m1"))
    step(lambda: api.set_vectors({"a2": V3, "b1": V3, "b2": V3}, "m1", {"a2": "h2", "b1": "stale"}))
    step(lambda: api.set_vectors({}, "m1"))

    step(lambda: api.upsert_one(_record("r1"), V1, "m1"))
    step(lambda: api.upsert_one(_record("r1"), V1, "m1"))  # the same text and a vector: nothing
    step(lambda: api.upsert_one(_record("r1"), None, "m2"))  # the same text, no vector offered: nothing
    step(lambda: api.upsert_one(_record("r1"), V2, "m2"))  # a vector for the new model
    step(lambda: api.upsert_one(_record("r1", content_hash="x", body="new words"), None, "m2"))  # plain text replaced
    step(lambda: api.upsert_one(_record("r2"), None, "m1", max_in_source=2))
    step(lambda: api.upsert_one(_record("r3"), V1, "m1", max_in_source=2))  # over the cap
    step(lambda: api.upsert_one(_record("r2", title="renamed", content_hash="y"), V1, "m1", max_in_source=2))  # an update is under it
    step(lambda: api.upsert_one(_record("o1", "other_plug"), V1, "m1"))
    step(lambda: api.upsert_one(_record("o2", "otherXplug"), V1, "m1"))
    step(lambda: api.upsert_one(_record("o3", "other_plug", "acct~_plugin_test_1"), None, "m1"))
    step(lambda: api.upsert_one(_record("o4", "pct%", "bob~s%"), None, "m1"))
    step(lambda: api.upsert_one(_record("s1", meta={"sealed": "enc1:not really"}), V3, "m1"))
    return out


def _deletes(api) -> list:
    out = []

    def step(call) -> None:
        try:
            answer = call()
        except ValueError as err:
            answer = f"refused: {err}"
        out.append((len(out), plain(answer), _snapshot()))

    step(lambda: api.delete_docs(doc_keys=["a1", "none", "s1"]))
    step(lambda: api.delete_docs(doc_keys=[]))
    step(lambda: api.drop_source(source_key="tools"))
    step(lambda: api.drop_source(source_key="none"))
    step(lambda: api.forget_records(plugin_id="other_plug"))  # `_` is no wildcard: otherXplug stays
    step(lambda: api.upsert_one(_record("o9", "other_plug"), V1, "m1"))  # refused: it is gone
    step(lambda: api.upsert_one(_doc("o9", plugin_id="other_plug"), None, "m1"))  # only records are refused
    step(lambda: api.clear_plugin_gone(plugin_id="other_plug"))
    step(lambda: api.upsert_one(_record("o9", "other_plug", "acct~_plugin_test_2"), V1, "m1"))
    step(lambda: api.drop_stale_sandbox_records(older_than_s=3600.0))
    step(lambda: api.drop_stale_sandbox_records(older_than_s=-60.0))
    step(lambda: api.drop_sources_like(prefix="record:pct%"))
    step(lambda: api.drop_scope_records(account="acct", scope_id="s1"))
    step(lambda: api.drop_scope_records(account="", scope_id="s1"))
    step(lambda: api.drop_sources_like(prefix="skills:"))
    return out


def test_the_index_is_written_the_same(rust_store) -> None:
    theirs = _steps(search) + _deletes(search)
    _clear()
    ours = _steps(_Rust(rust_store)) + _deletes(_Rust(rust_store))
    for mine, yours in zip(ours, theirs, strict=True):
        assert mine == yours
    refused = [answer for _, answer, *_ in theirs if str(answer).startswith("refused")]
    assert len(refused) == 2 and "at most 2 records" in refused[0] and "was uninstalled" in refused[1]


def test_a_wipe_keeps_the_plugin_records(rust_store) -> None:
    def wiped(api) -> dict:
        _clear()
        _steps(api)
        api.wipe()
        return _snapshot()

    theirs = wiped(search)
    assert wiped(_Rust(rust_store)) == theirs
    assert {row[2] for row in theirs["docs"]} == {"record"} and all(row[-1] is None for row in theirs["docs"]) and theirs["state"] == []


_QUERIES = ["body", "spawn actor", "run a workflow", "words NOT here", "é", "changed", "title a4", "renam", "the of", "", "a OR (b", "nothing-matches-this"]


def test_the_index_reads_the_same(rust_store) -> None:
    api = _Rust(rust_store)
    _steps(api)
    sources = [*search.source_keys(), "none"]
    keys = [row[0] for row in _sql("SELECT doc_key FROM search_docs")] + ["none"]
    ids = [row[0] for row in _sql("SELECT id FROM search_docs ORDER BY id")]
    assert sorted(api.source_keys()) == sorted(sources[:-1]) and len(sources) > 6
    assert api.tool_names() == plain(search.tool_names()) == ["spawn_actor"]
    assert api.states() == plain(search.states())
    assert api.counts() == search.counts() and api.has_docs() is True
    assert api.revision_token() == search.revision_token()
    for model in ("m1", "m2", "none"):
        for source in sources:
            assert api.existing(source_key=source, model=model) == plain(search.existing(source, model))
        for key in keys:
            assert api.record_state(doc_key=key, model=model) == plain(search.record_state(key, model))
        for limit in (5000, 2, 0):
            assert api.missing_vectors(model=model, limit=limit) == [dataclasses.asdict(doc) for doc in search.missing_vectors(model, limit)]
        got_ids, kinds, vecs = api.load_vectors(model=model)
        assert (got_ids, kinds, base64.b64decode(vecs)) == tuple(search.load_vectors(model))
    for source in sources:
        assert api.tool_owners(source_key=source) == search.tool_owners(source)
        assert api.count_source(source_key=source) == search.count_source(source)
        assert api.source_ids(source_key=source) == search.source_ids(source)
        assert api.get_state(source_key=source) == search.get_state(source)
    for key in keys:
        assert api.find_doc(doc_key=key) == search.find_doc(key)
    for some in (ids, ids[:2], [ids[0], ids[0], 999_999], []):
        assert api.get_docs(ids=some) == {str(doc_id): row for doc_id, row in search.get_docs(some, account="acct").items()}
    for query in _QUERIES:
        for kinds, source in (([], ""), (["tool", "record"], ""), (["nope"], ""), ([], "skills:a"), (["section"], "skills:a")):
            for limit in (50, 1, 0):
                assert api.keyword(text=query, kinds=kinds, limit=limit, source_key=source) == plain(
                    search.keyword(query, kinds, limit, source_key=source)
                ), (query, kinds, source, limit)
    for match in ('"body" OR "spawn"', '"bod"*', "", "((", '"é"'):
        assert api.keyword(text="ignored", match=match) == plain(search.keyword("ignored", match=match)), match
    for find in (
        {"kind": "tool", "tool_name": "spawn_actor"}, {"kind": "tool", "title": "SPAWN actor"}, {"kind": "pack", "pack_id": "pk"},
        {"kind": "section"}, {"kind": "section", "title": "Title A4", "pack_id": ""}, {"kind": "tool", "subskill_id": "none"},
    ):
        assert api.find_ids(**find) == search.find_ids(**find), find


def test_an_empty_index_reads_the_same(rust_store) -> None:
    api = _Rust(rust_store)
    assert api.has_docs() is False and search.has_docs() is False
    assert api.counts() == search.counts() and api.states() == {} and api.source_keys() == [] and api.tool_names() == []
    assert api.load_vectors(model="m1") == [[], [], ""] and api.get_docs(ids=[1]) == {} and api.keyword(text="body") == []
    assert api.revision_token() == search.revision_token()


@pytest.mark.parametrize("text", [
    "", "run a workflow", "the of and", "Spawn_Actor device-ref v2.0", "É ÉCOLE straße İstanbul ǅ", "a AND b OR NEAR(c) \"quoted\"* col:x",
    "ab", "abc", "日本語 テキスト ١٢٣ ⅷ ²", " ".join(f"word{i}" for i in range(30)), "same same SAME other", "  __  ", "x", "naïve café ﬁn",
])
def test_queries_are_made_the_same(rust_store, text: str) -> None:
    api = _Rust(rust_store)
    assert api.fts_query(text=text) == search.fts_query(text)
    assert api.fts_query(text=text, prefix=False) == search.fts_query(text, prefix=False)
    assert api.title_words(text=text) == search.title_words(text)


def test_sealed_records_open_for_their_account_only(rust_store, account: str) -> None:
    from backend.search import sealed_records
    from backend.store import account_rows

    key = account_rows.ready(account)
    api = _Rust(rust_store)
    mine = _record("mine", scope=f"{account}~s1", title="My title", body="my secret words", meta={"k": [1, "é"]})
    search.upsert_one(sealed_records.seal_doc(mine, account, key), sealed_records.seal_vec(V1, key), "m1")
    theirs = _record("theirs", scope="someone~s1", meta={"sealed": "enc1:" + _b64(b"another account's")})
    search.upsert_one(theirs, sealed_records.seal_vec(V2, b"k" * 32), "m1")
    broken = _record("broken", scope=f"{account}~s2", meta={"sealed": "enc1:" + _b64(b"damaged")})
    search.upsert_one(broken, sealed_records.VEC_MAGIC + b"too short", "m1")
    search.upsert_one(_record("plain", scope=f"{account}~s1"), V3, "m1")

    ids = [row[0] for row in _sql("SELECT id FROM search_docs ORDER BY id")]
    opened = search.get_docs(ids)
    assert api.get_docs(ids=ids) == {str(doc_id): row for doc_id, row in opened.items()}
    assert [(row["title"], row["meta"]) for row in opened.values()][:3] == [("My title", {"k": [1, "é"]}), ("", {}), ("", {})]
    assert "secret" not in str(_sql("SELECT title, body, meta FROM search_docs WHERE doc_key='mine'"))

    for who, with_key in ((account, key), (account, None), ("someone", key), ("", key)):
        got_ids, kinds, vecs = api.load_vectors(model="m1", account=who, key=None if with_key is None else _b64(with_key))
        assert (got_ids, kinds, base64.b64decode(vecs)) == tuple(search.load_vectors("m1", who, with_key)), (who, with_key)
    assert search.load_vectors("m1", account, key)[2] == V1 + V3
    # Sealed rows are never offered for a plain embed.
    assert api.missing_vectors(model="m2") == [dataclasses.asdict(doc) for doc in search.missing_vectors("m2")]
    assert [doc.doc_key for doc in search.missing_vectors("m2")] == ["plain"]
