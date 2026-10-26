"""ducky_list_archive: a group lists what was archived with it, nested groups included."""

from __future__ import annotations

from backend.tools.panel.ducky_panel import archive_tree


def _row(cid: str, parent: str = "", group: bool = False) -> dict:
    return {"id": cid, "title": cid, "is_group": group, "updated": 0.0, "parent_conv_id": parent}


def test_groups_hold_what_was_archived_with_them() -> None:
    rows = [
        _row("g1", group=True),
        _row("m1", "g1"),
        _row("g2", "g1", group=True),
        _row("m2", "g2"),
        _row("solo"),
        _row("orphan", "gone"),  # its group isn't in Archive: a top row of its own
    ]
    tree = archive_tree(rows)
    assert [n["id"] for n in tree] == ["g1", "solo", "orphan"]
    g1 = tree[0]
    assert g1["inside_count"] == 3
    assert [n["id"] for n in g1["inside"]] == ["m1", "g2"]
    assert g1["inside"][1]["inside_count"] == 1


def test_parent_loops_and_self_parents_are_still_listed() -> None:
    tree = archive_tree([_row("x", "y"), _row("y", "x"), _row("self", "self")])

    def ids(nodes: list[dict]) -> list[str]:
        return [i for n in nodes for i in [n["id"], *ids(n["inside"])]]

    assert sorted(ids(tree)) == ["self", "x", "y"]
