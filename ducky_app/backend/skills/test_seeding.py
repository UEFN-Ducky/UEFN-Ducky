"""A subskill dropped from a new build must not survive in AppData.

AppData outranks plugin-owned content in ``_pack_roots``, so a stale shipped
ref there is redeployed to every IDE — resurrecting removed content. Shipped
orphans must go; user/store/unstamped refs must never be touched.
"""

from __future__ import annotations

from pathlib import Path

import backend.skills.layout as layout
from backend.skills import seeding


def _pack(root: Path, pack_id: str, refs: dict[str, str], version: int = 1) -> Path:
    p = root / pack_id
    (p / layout.REFERENCES_DIR).mkdir(parents=True, exist_ok=True)
    (p / layout.PACK_FILE).write_text(
        "---\n"
        f"name: {pack_id}\n"
        f'description: "{pack_id}"\n'
        "metadata:\n"
        f"  version: {version}\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    for name, text in refs.items():
        (p / layout.REFERENCES_DIR / name).write_text(text, encoding="utf-8")
    return p


def _ref(origin: str = "", body: str = "text") -> str:
    if not origin:
        return f"# ref\n\n{body}\n"
    return "---\ndescription: \"r\"\nmetadata:\n" f"  origin: {origin}\n---\n\n{body}\n"


def test_shipped_orphan_is_pruned(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"kept.md": _ref()})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / layout.REFERENCES_DIR
    (refs / "kept.md").write_text(_ref(layout.ORIGIN_SHIPPED), encoding="utf-8")
    (refs / "dropped.md").write_text(
        _ref(layout.ORIGIN_SHIPPED, "wrong info removed in this build"), encoding="utf-8"
    )

    seeding._merge_pack_tree(src, dest, ref_origin=layout.ORIGIN_SHIPPED)

    assert not (refs / "dropped.md").exists(), "removed subskill survived in AppData"
    assert (refs / "kept.md").is_file()


def test_user_and_store_and_unstamped_refs_survive(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"kept.md": _ref()})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / layout.REFERENCES_DIR
    (refs / "mine.md").write_text(_ref(layout.ORIGIN_USER, "hand written"), encoding="utf-8")
    (refs / "from_layout.md").write_text(_ref(layout.ORIGIN_STORE), encoding="utf-8")
    (refs / "legacy.md").write_text(_ref(), encoding="utf-8")  # no stamp at all

    seeding._merge_pack_tree(src, dest, ref_origin=layout.ORIGIN_SHIPPED)

    assert (refs / "mine.md").is_file(), "deleted a user-authored subskill"
    assert (refs / "from_layout.md").is_file(), "deleted a store-owned subskill"
    assert (refs / "legacy.md").is_file(), "deleted an unstamped subskill"


def test_user_edit_of_a_shipped_ref_is_not_overwritten(tmp_path):
    src = _pack(tmp_path / "bundled", "verse", {"effects.md": _ref(body="shipped body")})
    dest = _pack(tmp_path / "appdata", "verse", {})
    refs = dest / layout.REFERENCES_DIR
    (refs / "effects.md").write_text(_ref(layout.ORIGIN_USER, "my edit"), encoding="utf-8")

    seeding._merge_pack_tree(src, dest, ref_origin=layout.ORIGIN_SHIPPED)

    assert "my edit" in (refs / "effects.md").read_text(encoding="utf-8")


def test_unchanged_license_is_not_rewritten(tmp_path, monkeypatch) -> None:
    # Every skill ship rewrote it: a disk write and an antivirus scan per pack, every 5 s
    # while a remote panel polled the listener.
    monkeypatch.setattr(seeding, "appdata_skill_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(seeding, "load_pack_manifest", lambda pid: {"id": pid, "label": "Ducky", "license": "MIT"})
    pack = tmp_path / "ducky"
    pack.mkdir()
    (pack / layout.PACK_FILE).write_text("---\nname: ducky\n---\n", encoding="utf-8")

    seeding._write_pack_license_file("ducky")
    license_file = pack / layout.LICENSE_FILE
    first = license_file.stat().st_mtime_ns
    text = license_file.read_text(encoding="utf-8")
    assert text

    seeding._write_pack_license_file("ducky")
    assert license_file.stat().st_mtime_ns == first

    monkeypatch.setattr(seeding, "load_pack_manifest", lambda pid: {"id": pid, "label": "Ducky", "license": "Apache-2.0"})
    seeding._write_pack_license_file("ducky")
    assert license_file.read_text(encoding="utf-8") != text


def test_seed_copies_bundled_packs_and_keeps_newer_appdata(tmp_path, monkeypatch) -> None:
    bundled = tmp_path / "bundled"
    _pack(bundled, "tips", {"a.md": _ref()}, version=2)
    _pack(bundled, "newer", {}, version=2)
    appdata = tmp_path / "appdata"
    _pack(appdata, "newer", {}, version=5)
    monkeypatch.setattr(seeding, "bundled_skill_packs_dir", lambda: bundled)
    monkeypatch.setattr(seeding, "appdata_skill_packs_dir", lambda: appdata)
    monkeypatch.setattr(seeding, "_current_app_version", lambda: "")
    monkeypatch.setattr(seeding, "load_pack_manifest", lambda pid: {"id": pid, "label": pid})

    logs = seeding.seed_skill_packs()

    assert any("tips v2 seeded" in ln for ln in logs), logs
    assert any("newer v5 kept" in ln for ln in logs), logs
    ref = (appdata / "tips" / layout.REFERENCES_DIR / "a.md").read_text(encoding="utf-8")
    assert f"origin: {layout.ORIGIN_SHIPPED}" in ref
