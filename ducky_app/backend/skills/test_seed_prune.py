"""A subskill dropped from a new build must not survive in AppData.

AppData outranks plugin-owned content in ``_pack_roots``, so a stale shipped
ref there is redeployed to every IDE — resurrecting removed content. Shipped
orphans must go; user/store/unstamped refs must never be touched.
"""

from __future__ import annotations

from pathlib import Path

import backend.skills.store as store


def _pack(root: Path, pack_id: str, refs: dict[str, str], version: int = 1) -> Path:
    p = root / pack_id
    (p / store.REFERENCES_DIR).mkdir(parents=True, exist_ok=True)
    (p / store.PACK_FILE).write_text(
        "---\n"
        f"name: {pack_id}\n"
        f'description: "{pack_id}"\n'
        "metadata:\n"
        f"  version: {version}\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    for name, text in refs.items():
        (p / store.REFERENCES_DIR / name).write_text(text, encoding="utf-8")
    return p


def _ref(origin: str = "", body: str = "text") -> str:
    if not origin:
        return f"# ref\n\n{body}\n"
    return "---\ndescription: \"r\"\nmetadata:\n" f"  origin: {origin}\n---\n\n{body}\n"


def test_shipped_orphan_is_pruned(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"kept.md": _ref()})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / store.REFERENCES_DIR
    (refs / "kept.md").write_text(_ref(store.ORIGIN_SHIPPED), encoding="utf-8")
    (refs / "dropped.md").write_text(
        _ref(store.ORIGIN_SHIPPED, "wrong info removed in this build"), encoding="utf-8"
    )

    store._merge_pack_tree(src, dest, ref_origin=store.ORIGIN_SHIPPED)

    assert not (refs / "dropped.md").exists(), "removed subskill survived in AppData"
    assert (refs / "kept.md").is_file()


def test_user_and_store_and_unstamped_refs_survive(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"kept.md": _ref()})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / store.REFERENCES_DIR
    (refs / "mine.md").write_text(_ref(store.ORIGIN_USER, "hand written"), encoding="utf-8")
    (refs / "from_store.md").write_text(_ref(store.ORIGIN_STORE), encoding="utf-8")
    (refs / "legacy.md").write_text(_ref(), encoding="utf-8")  # no stamp at all

    store._merge_pack_tree(src, dest, ref_origin=store.ORIGIN_SHIPPED)

    assert (refs / "mine.md").is_file(), "deleted a user-authored subskill"
    assert (refs / "from_store.md").is_file(), "deleted a store-owned subskill"
    assert (refs / "legacy.md").is_file(), "deleted an unstamped subskill"


def test_user_edit_of_a_shipped_ref_is_not_overwritten(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"effects.md": _ref(body="shipped body")})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / store.REFERENCES_DIR
    (refs / "effects.md").write_text(_ref(store.ORIGIN_USER, "my edit"), encoding="utf-8")

    store._merge_pack_tree(src, dest, ref_origin=store.ORIGIN_SHIPPED)

    assert "my edit" in (refs / "effects.md").read_text(encoding="utf-8")
