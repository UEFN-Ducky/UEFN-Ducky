"""Store skill updates merge: preserve user-created refs; stamp provenance."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.skill import (
    ORIGIN_USER,
    appdata_skill_packs_dir,
    build_skill_prompt,
    create_subskill,
    import_skill_pack_from_bytes,
    load_pack_manifest,
    parse_frontmatter,
    read_subskill_body,
)


def _pack_zip(
    pack_id: str,
    *,
    version: int = 1,
    core_body: str = "# Tips\n\nStore body v1.\n",
    refs: dict[str, str] | None = None,
) -> bytes:
    skill_md = (
        f"---\nname: {pack_id}\ndescription: store tips\n"
        f"metadata:\n  label: Tips\n  version: {version}\n---\n\n{core_body}"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", skill_md)
        for name, body in (refs or {"extra.md": "---\ndescription: extra\nmetadata:\n  label: Extra\n---\n\nExtra.\n"}).items():
            zf.writestr(f"references/{name}", body)
    return buf.getvalue()


@pytest.fixture()
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_store_install_stamps_source_and_slug(isolated_appdata: Path) -> None:
    del isolated_appdata
    raw = _pack_zip("demo-tips")
    result = import_skill_pack_from_bytes(
        raw, pack_id="demo-tips", replace=True, source="store", store_slug="demo-tips"
    )
    assert result.get("ok"), result
    man = load_pack_manifest("demo-tips")
    assert man is not None
    assert man.get("kind") == "store"
    assert man.get("source") == "store"
    assert man.get("store_slug") == "demo-tips"
    extra = next(s for s in man["subskills"] if s["id"] == "extra")
    assert extra.get("origin") == "store"


def test_store_update_preserves_user_ref(isolated_appdata: Path) -> None:
    del isolated_appdata
    import_skill_pack_from_bytes(
        _pack_zip("demo-tips", version=1, core_body="# Tips\n\nv1\n"),
        pack_id="demo-tips",
        replace=True,
        source="store",
        store_slug="demo-tips",
    )
    create_subskill("demo-tips", "my_notes", "My Notes", "user notes")
    user_path = appdata_skill_packs_dir() / "demo-tips" / "references" / "my_notes.md"
    assert user_path.is_file()
    meta, _ = parse_frontmatter(user_path.read_text(encoding="utf-8"))
    assert meta.get("metadata", {}).get("origin") == ORIGIN_USER

    import_skill_pack_from_bytes(
        _pack_zip(
            "demo-tips",
            version=2,
            core_body="# Tips\n\nv2 from store\n",
            refs={
                "extra.md": "---\ndescription: extra v2\nmetadata:\n  label: Extra\n---\n\nExtra v2.\n"
            },
        ),
        pack_id="demo-tips",
        replace=True,
        source="store",
        store_slug="demo-tips",
    )

    assert user_path.is_file(), "user-created ref must survive store update"
    body = read_subskill_body("demo-tips", "core") or ""
    assert "v2 from store" in body
    man = load_pack_manifest("demo-tips")
    assert man is not None
    ids = {s["id"] for s in man["subskills"]}
    assert "my_notes" in ids
    assert "extra" in ids
    notes = next(s for s in man["subskills"] if s["id"] == "my_notes")
    assert notes.get("origin") == "user"


def test_index_labels_yours_and_store(isolated_appdata: Path) -> None:
    del isolated_appdata
    import_skill_pack_from_bytes(
        _pack_zip("demo-tips"),
        pack_id="demo-tips",
        replace=True,
        source="store",
        store_slug="demo-tips",
    )
    create_subskill("demo-tips", "my_notes", "My Notes", "user notes")
    from backend.skill import SkillSelection

    text = build_skill_prompt(SkillSelection(disabled_packs=[]))
    assert "`demo-tips` [store]" in text
    assert "`my_notes` [yours]" in text
    assert "`extra` [store]" in text
