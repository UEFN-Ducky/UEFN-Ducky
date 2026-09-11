"""Filename tokens and FTS join so 'virtual pointer' hits virtual_pointer_device."""

from frontend.ui_web.workspace_search import tokens_match
from backend.store.repos.chats import _fts_query


def test_tokens_match_underscores() -> None:
    assert tokens_match("virtual pointer", "Content/Verse/VirtualPointer/virtual_pointer_device.verse")
    assert not tokens_match("virtual pointer", "Content/Verse/DuckyTests/ducky_test_device.verse")


def test_fts_query_also_tries_underscore_join() -> None:
    q = _fts_query("virtual pointer")
    assert '"virtual"' in q and '"pointer"' in q
    assert '"virtual_pointer"' in q
