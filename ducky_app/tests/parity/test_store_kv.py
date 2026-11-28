"""The key -> JSON document tables and ``meta``."""

from __future__ import annotations

import pytest

from backend.store.repos import kv

from .parity_harness import callers
from .test_store_chats import _sql

_VALUES = [{"a": 1, "é": [1.5, None, True]}, [1, "two"], "text é", 0, None, 2.5, {}, {"nested": {"z": 1, "a": 2}}]


def _rows(table: str) -> list:
    return _sql(f"SELECT key, value FROM {table} WHERE key LIKE 't:%' ORDER BY key")


@pytest.mark.parametrize("table", ["settings", "workspace_state", "cache_docs"])
def test_documents_are_stored_and_read_the_same(rust_store, table: str) -> None:
    python, rust = callers(rust_store, "kv", kv)

    def write(call) -> list:
        _sql(f"DELETE FROM {table} WHERE key LIKE 't:%'")
        for index, value in enumerate(_VALUES):
            call("set_doc", table=table, key=f"t:p:{index}", value=value)
        call("set_doc", table=table, key="t:p:0", value={"replaced": True})
        call("set_doc", table=table, key="t:q_1", value="other prefix")
        call("delete_doc", table=table, key="t:p:3")
        call("delete_doc", table=table, key="t:none")
        return _rows(table)

    theirs = write(python)
    assert write(rust) == theirs and len(theirs) == len(_VALUES)
    _sql(f"INSERT INTO {table}(key, value, updated) VALUES ('t:bad', '{{oops', 0)")
    for key in ("t:p:0", "t:p:1", "t:p:4", "t:p:3", "t:bad", "t:none"):
        assert rust("get_doc", table=table, key=key) == kv.get_doc(table, key)
    for prefix in ("t:", "t:p:", "t:q_", "t:p:1", "none"):
        assert rust("list_docs", table=table, prefix=prefix) == kv.list_docs(table, prefix)


@pytest.mark.parametrize(("prefix", "keep"), [("t:p:", 3), ("t:p:", 0), ("t:p:", 100), ("t:", 2), ("none", 1), ("t:p:1", 0)])
def test_a_prune_keeps_the_highest_keys(rust_store, prefix: str, keep: int) -> None:
    python, rust = callers(rust_store, "kv", kv)

    def pruned(call) -> tuple:
        _sql("DELETE FROM cache_docs")
        for index in range(12):
            kv.set_doc("cache_docs", f"t:p:{index:02d}", index)
        kv.set_doc("cache_docs", "t:q", "stays unless the prefix covers it")
        return call("prune_docs", table="cache_docs", prefix=prefix, keep=keep), _rows("cache_docs")

    assert pruned(rust) == pruned(python)


def test_meta_values_and_tables_that_are_not_documents(rust_store) -> None:
    rust_store.store("kv", "meta_set", key="t:one", value="é value")
    assert kv.meta_get("t:one") == "é value" == rust_store.store("kv", "meta_get", key="t:one")
    kv.meta_set("t:one", "again")
    assert rust_store.store("kv", "meta_get", key="t:one") == "again" and rust_store.store("kv", "meta_get", key="t:none") is None
    for function, args in (("get_doc", {}), ("set_doc", {"value": 1}), ("delete_doc", {}), ("list_docs", {"prefix": ""}), ("prune_docs", {"prefix": "a", "keep": 0})):
        status, reply = rust_store.request(f"/__rs/store/kv/{function}", {"table": "messages; DROP TABLE meta", "key": "k", **args})
        assert status == 200 and reply == {"ok": False, "error": "messages; DROP TABLE meta is not a document table"}
    status, reply = rust_store.request("/__rs/store/kv/prune_docs", {"table": "cache_docs", "prefix": "", "keep": 0})
    assert reply == {"ok": False, "error": "a prune needs a prefix"}
