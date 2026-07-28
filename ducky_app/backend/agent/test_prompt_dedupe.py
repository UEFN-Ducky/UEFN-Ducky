"""Exact-block prompt dedupe — paste-sized copies only."""

from __future__ import annotations

from backend.agent.prompt_dedupe import dedupe_exact_blocks

_BIG = "A" * 80


def test_drops_exact_duplicate_paste_block():
    assert dedupe_exact_blocks(f"{_BIG}\n\n{_BIG}") == _BIG


def test_keeps_short_repeats():
    assert dedupe_exact_blocks("ok\n\nok") == "ok\n\nok"


def test_fenced_block_is_atomic():
    fenced = "```\n" + ("x" * 80) + "\n```"
    assert dedupe_exact_blocks(f"{fenced}\n\n{fenced}") == fenced


def test_normalizes_crlf():
    assert dedupe_exact_blocks(f"{_BIG}\r\n\r\n{_BIG}") == _BIG
