"""One skills pass: values computed once inside it, never shared across passes."""

from __future__ import annotations

from backend.skills import passes


def test_pass_cached_computes_once_per_pass() -> None:
    calls: list[int] = []

    def compute() -> int:
        calls.append(1)
        return len(calls)

    assert passes.pass_cached("k", compute) == 1  # no pass open: uncached
    assert passes.pass_cached("k", compute) == 2
    with passes.pass_memo():
        assert passes.pass_cached("k", compute) == 3
        assert passes.pass_cached("k", compute) == 3
    with passes.pass_memo():
        assert passes.pass_cached("k", compute) == 4  # a new pass starts empty


def test_shared_pass_memo_joins_an_open_pass() -> None:
    with passes.pass_memo():
        passes.pass_cached("k", lambda: "outer")
        with passes.shared_pass_memo():
            assert passes.pass_cached("k", lambda: "inner") == "outer"
    with passes.shared_pass_memo():
        assert passes._PASS_MEMO.get() == {}
    assert passes._PASS_MEMO.get() is None
