"""The plugin key/value repo: plugin cache documents and prefs, per account and scope."""

from __future__ import annotations

from backend.store.repos import plugin_kv

from parity_harness import callers, plain
from test_store_chats import _sql

MINE = {"account": "acct", "scope": "s1", "plugin_id": "plug"}


def _rows() -> list:
    return _sql("SELECT account_id, scope_id, plugin_id, key, value, encrypted FROM plugin_kv ORDER BY 1, 2, 3, 4")


def _steps(call) -> list:
    """The same calls through either backend: each answer with the rows it left."""
    _sql("DELETE FROM plugin_kv")
    out = []

    def step(function: str, **args) -> None:
        out.append((function, args, plain(call(function, **args)), _rows()))

    for key, value in (("a", {"n": 1, "é": [1.5, None]}), ("a_b", "text"), ("a%c", 0), ("axb", None), ("é:1", [1]), ("é:2", 2.5), ("__prefs__x", {})):
        step("set", key=key, value=value, **MINE)
    step("set", key="a", value={"replaced": True}, **MINE)
    step("set", key="secret", value=None, encrypted_b64="c2VhbGVk", **MINE)
    step("set", key="a", value="another scope", **{**MINE, "scope": "s2"})
    step("set", key="a", value="another account", **{**MINE, "account": "bob"})
    step("set", key="a", value="another plugin", **{**MINE, "plugin_id": "other"})
    step("set", key="legacy", value="unclaimed", **{**MINE, "account": ""})
    step("set", key="a", value="unclaimed, and acct has one", **{**MINE, "account": ""})
    step("set_prefs", sealed="cHJlZnM=", **MINE)
    step("set_prefs", sealed="b3RoZXI=", **{**MINE, "plugin_id": "other"})
    for key in ("a", "secret", "none", "__prefs__", "axb"):
        step("get", key=key, **MINE)
    for prefix in ("", "a", "a_", "a%", "é:", "é", "none", "__prefs__"):
        step("keys", prefix=prefix, **MINE)
    step("all_prefs", account="acct", scope="s1")
    step("all_prefs", account="acct", scope="none")
    step("has_rows")
    step("has_unclaimed")
    step("unsealed_rows", account="acct", prefix="c2")
    step("unsealed_rows", account="acct", prefix="")
    step("replace_value", key="a_b", value="enc1:sealed", old='"text"', **MINE)
    step("replace_value", key="a%c", value="enc1:sealed", old="changed meanwhile", **MINE)
    step("claim_unclaimed", account="acct")
    step("claim_unclaimed", account="acct")
    step("has_unclaimed")
    step("delete", key="a", **MINE)
    step("delete", key="a", **MINE)
    step("delete_prefix", prefix="a_", **MINE)
    step("delete_prefix", prefix="é:", **MINE)
    step("delete_prefix", prefix="none", **MINE)
    step("delete_prefix", prefix="", **MINE)  # everything but the prefs
    step("delete_scope", account="acct", scope="s2")
    step("delete_scope", account="none", scope="s1")
    return out


def test_plugin_values_are_stored_and_read_the_same(rust_store) -> None:
    python, rust = callers(rust_store, "plugin_kv", plugin_kv)
    theirs = _steps(python)
    for mine, yours in zip(_steps(rust), theirs, strict=True):
        assert mine == yours
    answers = {(function, args.get("key") or args.get("prefix")): answer for function, args, answer, _ in theirs}
    assert answers["get", "secret"] == ["c2VhbGVk", True] and answers["keys", "a_"] == ["a_b"] and answers["claim_unclaimed", None] == 0
    left = [row[2:4] for row in theirs[-1][3] if row[:2] == ("acct", "s1")]
    assert left == [("other", "__prefs__"), ("other", "a"), ("plug", "__prefs__")]


def test_an_empty_table(rust_store) -> None:
    _, rust = callers(rust_store, "plugin_kv", plugin_kv)
    assert rust("has_rows") is False and rust("has_unclaimed") is False and rust("get", key="k", **MINE) == [None, False]
    assert rust("keys", prefix="", **MINE) == [] and rust("all_prefs", account="acct", scope="s1") == {}
