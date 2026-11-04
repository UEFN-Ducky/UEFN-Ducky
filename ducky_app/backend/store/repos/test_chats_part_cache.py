"""Opened chat parts are kept process-wide, so a part is opened (DPAPI) once, not per read."""

from __future__ import annotations

import threading
import time
import zlib

import pytest

from backend.store import account_rows
from backend.store.repos import chats as repo

PROJECT = "proj_part_cache"


@pytest.fixture(autouse=True)
def _empty_cache():
    repo.forget_parts()
    yield
    repo.forget_parts()


def _counting_open(monkeypatch) -> list[str]:
    """Count part opens; the real open still runs."""
    calls: list[str] = []
    real = account_rows.open_blob

    def counted(raw: bytes, account: str) -> bytes:
        calls.append(account)
        return real(raw, account)

    monkeypatch.setattr(account_rows, "open_blob", counted)
    return calls


def _fake_open(monkeypatch) -> list[str]:
    """Parts as plain zlib (no key): for reader-level tests with made-up blobs."""
    calls: list[str] = []
    monkeypatch.setattr(account_rows, "open_blob", lambda raw, account: calls.append(account) or raw)
    return calls


def _result(seed: str, size: int = 20_000) -> str:
    line = f"{seed}: tool output line for the part cache\n"
    return (line * (size // len(line) + 1))[:size]


def _assistant(*texts: str) -> dict:
    blocks = [{"type": "tool_call", "id": f"c{i}", "name": "workspace_read_file", "status": "success",
               "arguments": {}, "result": {"ok": True, "text": t}} for i, t in enumerate(texts)]
    return {"role": "assistant", "content": "done", "ts": time.time(), "blocks": blocks}


def _drop_row_cache() -> None:
    # The keeper's chats fall out of the 8-chat row cache; this is that case.
    with repo._opened_lock:
        repo._opened.clear()
        repo._opened_size = 0


def test_a_thousand_reads_open_each_part_once(monkeypatch) -> None:
    texts = [_result("a"), _result("b"), _result("c")]
    messages = [{"role": "user", "content": "go", "ts": 1.0}, _assistant(*texts)]
    repo.conv_save(PROJECT, {"id": "pc1", "title": "pc1", "created": 1.0, "updated": 1.0}, messages=messages)
    repo.forget_parts()
    calls = _counting_open(monkeypatch)

    t0 = time.perf_counter()
    for _ in range(1000):
        _drop_row_cache()
        got = repo.messages_get("pc1")
        assert [b["result"]["text"] for b in got[1]["blocks"]] == texts
    took = time.perf_counter() - t0
    print(f"1,000 reads: opens {len(calls)} (was {3 * 1000}); {took * 1000:.0f} ms")
    assert len(calls) == 3


def test_another_account_opens_the_same_digest_itself(monkeypatch) -> None:
    calls = _fake_open(monkeypatch)
    blob = zlib.compress(b'"secret"')
    for account in ("acct_a", "acct_a", "acct_b", "acct_b"):
        assert repo._part_reader(lambda: {"d1": blob}, account)("d1") == '"secret"'
    assert calls == ["acct_a", "acct_b"]
    assert repo._part_cached("acct_c", "d1") is None


def test_the_size_bound_holds_over_ten_thousand_parts(monkeypatch) -> None:
    _fake_open(monkeypatch)
    monkeypatch.setattr(repo, "_PARTS_MAX_CHARS", 200_000)
    packed = {f"d{i}": zlib.compress(("x%05d" % i * 20).encode()) for i in range(10_000)}
    read = repo._part_reader(lambda: packed, "acct")
    for i in range(10_000):
        assert read(f"d{i}") == "x%05d" % i * 20
    assert repo._parts_chars == sum(len(t) for t in repo._parts_open.values()) <= 200_000
    assert ("acct", "d9999") in repo._parts_open and ("acct", "d0") not in repo._parts_open


def test_a_part_bigger_than_the_cache_is_not_kept(monkeypatch) -> None:
    _fake_open(monkeypatch)
    monkeypatch.setattr(repo, "_PARTS_MAX_CHARS", 10)
    assert repo._part_reader(lambda: {"big": zlib.compress(b"y" * 50)}, "acct")("big") == "y" * 50
    assert repo._parts_chars == 0 and not repo._parts_open


def test_sixteen_threads_read_correct_text(monkeypatch) -> None:
    _fake_open(monkeypatch)
    monkeypatch.setattr(repo, "_PARTS_MAX_CHARS", 5_000)  # small: evictions race the reads
    packed = {f"d{i}": zlib.compress(f"part {i} ".encode() * 10) for i in range(500)}
    errors: list[str] = []

    def work(seed: int) -> None:
        read = repo._part_reader(lambda: packed, "acct")
        for n in range(2000):
            i = (n * 7 + seed) % 500
            if read(f"d{i}") != f"part {i} " * 10:
                errors.append(f"d{i}")

    threads = [threading.Thread(target=work, args=(s,)) for s in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert repo._parts_chars == sum(len(t) for t in repo._parts_open.values()) <= 5_000


def test_last_reply_opens_only_the_tail(monkeypatch) -> None:
    old = [_assistant(_result(f"old{i}")) for i in range(20)]
    last = {"role": "assistant", "content": "  BLOCKED: needs the CLI  ", "ts": 2.0,
            "blocks": [{"type": "tool_call", "result": {"text": _result("tail")}}]}
    messages = [{"role": "user", "content": "go", "ts": 1.0}, *old, last,
                {"role": "assistant", "content": "", "ts": 3.0}]
    repo.conv_save(PROJECT, {"id": "pc2", "title": "pc2", "created": 1.0, "updated": 1.0}, messages=messages)
    repo.forget_parts()
    _drop_row_cache()
    calls = _counting_open(monkeypatch)
    assert repo.last_reply("pc2") == ("BLOCKED: needs the CLI", 22)
    assert len(calls) == 1  # the tail's one part; the 20 older replies stay closed
    assert repo.last_reply("no-such-chat") == ("", 0)


def test_a_missing_part_reads_as_none(monkeypatch) -> None:
    _fake_open(monkeypatch)
    assert repo._part_reader(lambda: {}, "acct")("nope") is None
