"""Offline digest engine — discovery, ranked search, catalogs, mtime cache."""

from __future__ import annotations

import os
import time
from pathlib import Path

from backend.tools.verse import verse_digests as vd

SAMPLE = """\
Fortnite := module:
    Devices := module:
        # A button players can press.
        button_device := class(creative_device):
            # Fires when pressed.
            PressedEvent<public>:listenable(agent) = external {}
            Enable<public>():void = external {}

        # Not a device — ignore for device list.
        some_helper := class:
            Help<public>():void = external {}

        trigger_device := class(creative_device):
            TriggeredEvent<public>:listenable(?agent) = external {}

    Items := module:
        # Grants an item to the agent.
        item_grants := class:
            Pass<public>():void = external {}

(entity:entity).GetComponent<public>()<transacts><decides>:component
"""


def _write_digest(tmp: Path, name: str = "Fortnite.digest.verse", text: str = SAMPLE) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def test_discovery_and_list_digests(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    found = vd.discover_digest_files(digest_path=str(path))
    assert found == [str(path)]

    catalog = vd.list_verse_digests(digest_path=str(path))
    assert catalog["count"] == 1
    entry = catalog["digests"][0]
    assert entry["name"] == "Fortnite.digest.verse"
    assert "Epic" in entry["purpose"] or "device" in entry["purpose"].lower()
    assert entry["decl_total"] >= 4
    assert entry["decl_counts"].get("class", 0) >= 2
    assert entry["decl_counts"].get("module", 0) >= 2


def test_ranked_search_prefers_decl_name(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    result = vd.search_verse_digest("button_device", digest_path=str(path))
    assert result["count"] >= 1
    top = result["matches"][0]
    assert "button_device" in top["text"]
    assert top["rank"] == 0
    assert "Devices" in (top.get("module") or "")


def test_get_verse_api_extracts_block(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    result = vd.get_verse_api("button_device", digest_path=str(path))
    assert result["count"] == 1
    block = result["matches"][0]
    assert block["kind"] == "class"
    assert "PressedEvent" in block["definition"]
    assert "A button players can press" in block["definition"]
    assert "Devices" in block["module"]


def test_list_verse_types_and_devices(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    types = vd.list_verse_types(
        kind="class", name_filter="_device", digest_path=str(path)
    )
    names = {t["name"] for t in types["types"]}
    assert "button_device" in names
    assert "trigger_device" in names
    assert "some_helper" not in names

    devices = vd.list_verse_devices(digest_path=str(path))
    assert "button_device" in devices["devices"]
    assert "trigger_device" in devices["devices"]
    assert "some_helper" not in devices["devices"]
    assert devices["count"] == 2


def test_list_verse_modules(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    mods = vd.list_verse_modules(digest_path=str(path))
    module_names = {m["module"] for m in mods["modules"]}
    assert "Fortnite" in module_names
    assert "Fortnite.Devices" in module_names


def test_cache_invalidates_on_mtime(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path)
    first = vd.get_verse_api("button_device", digest_path=str(path))
    assert first["count"] == 1

    time.sleep(0.05)
    new_text = SAMPLE.replace("button_device", "switch_device").replace(
        "A button players can press", "A switch device."
    )
    path.write_text(new_text, encoding="utf-8")
    now = time.time() + 1
    os.utime(path, (now, now))

    missing = vd.get_verse_api("button_device", digest_path=str(path))
    assert missing["count"] == 0
    found = vd.get_verse_api("switch_device", digest_path=str(path))
    assert found["count"] == 1
    assert "A switch device" in found["matches"][0]["definition"]


def test_assets_purpose_blurb(tmp_path: Path):
    vd.clear_cache()
    path = _write_digest(tmp_path, name="Assets.digest.verse", text="MyMats := module:\n")
    catalog = vd.list_verse_digests(digest_path=str(path))
    entry = catalog["digests"][0]
    assert "custom" in entry["purpose"].lower()


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_discovery_and_list_digests(p)
        test_ranked_search_prefers_decl_name(p)
        test_get_verse_api_extracts_block(p)
        test_list_verse_types_and_devices(p)
        test_list_verse_modules(p)
        test_cache_invalidates_on_mtime(p)
        test_assets_purpose_blurb(p)
    print("test_verse_digests: ok")


if __name__ == "__main__":
    main()


def test_a_bare_digest_name_finds_that_project_digest(tmp_path: Path, monkeypatch):
    # Oct 17 2026: digest_path="Fortnite.digest.verse" fell back to the UEFN listener and
    # search_verse_digest refused with UEFN closed.
    vd.clear_cache()
    monkeypatch.setattr(vd, "_verse_project_dir", lambda _name="": "")
    monkeypatch.setattr(vd, "_project_root", lambda: "")
    content = tmp_path / "Content"
    content.mkdir()
    path = _write_digest(content)
    _write_digest(content, name="Verse.digest.verse", text="Verse := module:\n")
    roots = [str(content)]
    assert vd.discover_digest_files(digest_path="Fortnite.digest.verse", extra_roots=roots) == [str(path)]
    assert vd.discover_digest_files(digest_path="Missing.digest.verse", extra_roots=roots) == []
    assert vd.digests_available(digest_path="fortnite.digest.verse", extra_roots=roots)


def test_a_new_assets_digest_is_found_without_waiting_for_the_memo(tmp_path: Path, monkeypatch):
    # Oct 26 2026: the 30 s discovery memo hid the first Assets digest after a build.
    vd.clear_cache()
    monkeypatch.setattr(vd, "_verse_project_dir", lambda _name="": "")
    monkeypatch.setattr(vd, "_project_root", lambda: "")
    builtin = tmp_path / "Digests" / "BuiltIn" / "Fortnite"
    builtin.mkdir(parents=True)
    _write_digest(builtin)
    roots = [str(tmp_path)]
    assert len(vd.discover_digest_files(extra_roots=roots)) == 1
    assets = tmp_path / "Digests" / "Island-Assets"
    assets.mkdir()
    _write_digest(assets, name="Assets.digest.verse", text="Island := module:\n")
    # Linux stamps a folder with a coarse clock (~4 ms): this mkdir can keep the mtime the
    # first scan saw. UEFN's build adds the folder seconds later; move the stamp like that did.
    st = (tmp_path / "Digests").stat()
    os.utime(tmp_path / "Digests", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    names = [os.path.basename(p) for p in vd.discover_digest_files(extra_roots=roots)]
    assert sorted(names) == ["Assets.digest.verse", "Fortnite.digest.verse"]
    # Nothing moved: the memo answers again.
    monkeypatch.setattr(vd.os, "walk", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("walked")))
    assert len(vd.discover_digest_files(extra_roots=roots)) == 2


def test_search_ignores_an_index_older_than_the_digest(tmp_path: Path, monkeypatch):
    # Oct 26 2026: a re-index that failed (busy DB) left the trigram index on the old
    # digest, and search_verse_digest hid everything UEFN added since.
    class StaleRows:
        def digest_indexed_mtime(self, _path):
            return 1.0  # never the file's mtime

        def digest_index(self, *_a):
            raise RuntimeError("database is locked")

        def digest_search(self, _q, paths, limit=0):
            assert paths == []
            return []

    vd.clear_cache()
    monkeypatch.setattr(vd, "_use_db", lambda: True)
    monkeypatch.setattr(vd, "_rows", lambda: StaleRows())
    path = _write_digest(tmp_path)
    out = vd.search_verse_digest("trigger_device", digest_path=str(path))
    assert out["count"] >= 1 and out["matches"][0]["rank"] == 0
