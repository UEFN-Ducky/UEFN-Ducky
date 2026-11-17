"""A big file rewritten whole to change a few lines gets a tip to edit instead."""

from __future__ import annotations

import pytest

from backend.tools.core.system import rewrite_tip


@pytest.mark.parametrize("chars,added,removed,want", [
    (10_000, 2, 2, True),       # 2 of ~400 lines: should have been an edit
    (10_000, 200, 200, False),  # half the file changed: a rewrite is fine
    (10_000, 400, 0, False),    # a new file
    (2_000, 1, 1, False),       # small file: not worth a note
])
def test_a_big_rewrite_of_a_few_lines_gets_the_edit_tip(chars, added, removed, want) -> None:
    content = ("x" * 24 + "\n") * (chars // 25)
    assert bool(rewrite_tip(content, added, removed)) is want
