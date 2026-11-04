"""SKILL.md frontmatter round trips and the provenance tags shown to agents."""

from __future__ import annotations

from pathlib import Path

from backend.skills import frontmatter as fm


def test_parse_and_serialize_round_trip() -> None:
    text = (
        "---\nname: demo\ndescription: \"Demo: tips\"\nlicense: MIT\n"
        "metadata:\n  label: Demo\n  version: 2\n  allow_redistribute: false\n---\n\n# Body\n"
    )
    meta, body = fm.parse_frontmatter(text)
    assert meta["name"] == "demo" and meta["description"] == "Demo: tips"
    assert meta["metadata"] == {"label": "Demo", "version": 2, "allow_redistribute": False}
    assert body == "# Body\n"
    again, body2 = fm.parse_frontmatter(fm.serialize_frontmatter(meta, body))
    assert again == meta and body2.strip() == "# Body"
    assert fm.parse_frontmatter("no frontmatter") == ({}, "no frontmatter")
    assert fm.parse_frontmatter("---\nname: x\n") == ({}, "---\nname: x\n")  # unterminated


def test_commercial_fields_and_origin_stamp(tmp_path: Path) -> None:
    meta, _ = fm.parse_frontmatter(
        "---\nlicense: MIT\nmetadata:\n  author: Ana\n  allow_redistribute: true\n  origin: User\n---\n"
    )
    assert fm._commercial_fields_from_meta(meta) == {
        "license": "MIT", "author": "Ana", "allow_redistribute": True, "origin": "User",
    }
    assert fm._origin_from_meta(meta) == "user"
    stamped = fm._stamp_ref_origin(b"# ref\n", "shipped")
    assert fm._origin_from_meta(fm.parse_frontmatter(stamped)[0]) == "shipped"
    ref = tmp_path / "mine.md"
    ref.write_text(fm._stamp_ref_origin("# mine\n", "user"), encoding="utf-8")
    assert fm._is_user_origin_file(ref) and not fm._is_user_origin_file(tmp_path / "gone.md")


def test_index_tags() -> None:
    assert fm.pack_index_tag({"kind": "plugin"}) == "plugin"
    assert fm.pack_index_tag({"kind": "bundled"}) == "shipped"
    assert fm.pack_index_tag({"kind": "custom", "source": "store"}) == "store"
    assert fm.pack_index_tag({"kind": "custom"}) == "yours"
    assert fm.subskill_index_tag({"id": "x", "origin": "user"}, {"kind": "bundled"}) == "yours"
    assert fm.subskill_index_tag({"id": "x"}, {"kind": "custom"}) == "shipped"  # untagged ref in a user pack
    assert fm.subskill_index_tag({"id": "core"}, {"kind": "custom"}) == "yours"


def test_license_text() -> None:
    closed = fm._license_text_for_pack({"label": "Tips", "author": "Ana"})
    assert "All Rights Reserved" in closed and "Copyright (c) Ana" in closed and '"Tips"' in closed
    mit = fm._license_text_for_pack({"license": "MIT", "allow_redistribute": True})
    assert "Permission is hereby granted" in mit
