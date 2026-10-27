"""Skill packs and tools become search rows."""

from __future__ import annotations

from pathlib import Path

from backend.search import chunker


def _pack(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _manifest(root: Path, pack_id: str) -> dict:
    from backend.skills.store import _manifest_from_skill_dir

    return _manifest_from_skill_dir(root, pack_id, "custom")


def test_split_sections_ignores_headings_inside_code() -> None:
    body = "intro line\n\n## First\ntext one\n```md\n## not a heading\n```\n### Second `code`\ntext two\n"
    intro, sections = chunker.split_sections(body)
    assert intro == "intro line"
    assert [h for h, _ in sections] == ["First", "Second `code"]
    assert "## not a heading" in sections[0][1]


def test_long_sections_split_on_paragraphs_and_keep_code_whole() -> None:
    para = "word " * 150  # ~750 chars
    code = "```\n" + ("x = 1\n" * 300) + "```"  # > 1,200 chars
    pieces = chunker.split_long(f"{para}\n\n{para}\n\n{code}")
    assert len(pieces) >= 3
    assert all(len(p) <= chunker.CODE_BLOCK_MAX for p in pieces)
    assert any(p.startswith("```") for p in pieces)


def test_pack_docs_rows_keys_and_paths(tmp_path: Path) -> None:
    root = _pack(tmp_path / "demo", {
        "SKILL.md": "---\nname: demo\ndescription: Demo pack for night skies\nkeywords: stars, sky\nmetadata:\n  label: Demo\n---\n\n# Demo\n\nCore intro.\n\n## Rules\nDo the thing.\n",
        "references/sky.md": "---\ndescription: Starfield material recipe\nmetadata:\n  label: Sky\n---\n\n## Stars\nMake stars.\n\n## Stars\nAgain.\n",
        "references/plain.md": "No front matter and no headings, just a paragraph long enough to index on its own as text.\n" * 3,
        "references/long.md": "A long file with no headings at all, so its body becomes sections of its own.\n\n" * 12,
    })
    docs = chunker.pack_docs("demo", root, _manifest(root, "demo"), origin="user")
    keys = [d.doc_key for d in docs]
    assert keys[0] == "skill:demo"
    assert "skill:demo/core" in keys and "skill:demo/core#rules" in keys
    assert "skill:demo/sky#stars" in keys and "skill:demo/sky#stars-2" in keys  # duplicate headings
    # No headings: a short body lives in the file row only; a long one becomes sections too.
    assert "skill:demo/plain" in keys and not any(k.startswith("skill:demo/plain#") for k in keys)
    assert any(k.startswith("skill:demo/long#") for k in keys)
    # A file row lists its sections; the details stay in the section rows.
    sky_file = next(d for d in docs if d.doc_key == "skill:demo/sky")
    assert "Sections: Stars; Stars" in sky_file.body and "Make stars." not in sky_file.body
    sky = next(d for d in docs if d.doc_key == "skill:demo/sky#stars")
    assert sky.path == "Demo > Sky > Stars" and sky.kind == "section" and sky.section == "Stars"
    pack = docs[0]
    assert "stars, sky" in pack.body and "Sky (sky)" in pack.body
    assert all(d.content_hash for d in docs)
    # Same files, same rows: hashes are stable.
    again = chunker.pack_docs("demo", root, _manifest(root, "demo"), origin="user")
    assert [d.content_hash for d in again] == [d.content_hash for d in docs]


def test_tool_docs_words_owner_and_params() -> None:
    rows = [{
        "name": "wire_verse_device_ref",
        "description": "Assign one device to a Verse @editable field.\nMore text.",
        "inputSchema": {"properties": {"device": {"description": "the device label"}, "field": {}}},
        "aliases": ["wire_device"],
        "owner_label": "Plugin: UEFN",
        "plugin_id": "uefn",
    }, {"name": "srv__ping", "description": ""}, {"name": ""}]
    docs = chunker.tool_docs(rows)
    assert [d.tool_name for d in docs] == ["wire_verse_device_ref", "srv__ping"]
    wire = docs[0]
    assert wire.title == "wire verse device ref" and wire.path == "Plugin: UEFN" and wire.origin == "plugin"
    assert "device (the device label)" in wire.body and "wire_device" in wire.body
    assert wire.meta["description"] == "Assign one device to a Verse @editable field."
    # The first line moves to the summary column (weighted on its own); the rest stays in the body.
    assert wire.summary == "Assign one device to a Verse @editable field."
    assert "Assign one device" not in wire.body and "More text." in wire.body
    assert docs[1].origin == "mcp" and docs[1].title == "ping" and docs[1].summary == ""


def test_a_plugin_tool_carries_what_its_plugin_is_for() -> None:
    # Oct 26 2026: "search team assets I have" ranked site storage tools over Island Media Kit's.
    row = {"name": "imk_media_list", "description": "List a project's media.", "owner_label": "Plugin: Island Media Kit",
           "owner_about": "Make island trailers from your own recordings", "plugin_id": "island-media-kit"}
    doc = chunker.tool_docs([row])[0]
    assert "Island Media Kit: Make island trailers from your own recordings" in doc.body
    assert "Plugin: Plugin" not in doc.body
    assert chunker.tool_docs([{**row, "owner_about": ""}])[0].body == "imk media list"


def test_embed_text_and_snippet() -> None:
    doc = chunker.record_doc("wiki", "personal", "p1", "Spawner", "How spawners work.", {"url": "x"})
    assert chunker.embed_text(doc).startswith("Spawner")
    assert doc.doc_key == "record:wiki:personal:p1" and doc.meta == {"url": "x"}
    assert chunker.snippet("```py\nprint(1)\n```  and   more", 12).endswith("…")
    assert chunker.slug("Prefer Epic first (HARD)") == "prefer-epic-first-hard"
