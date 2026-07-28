"""Offline digest engine — discovery, ranked search, catalogs, mtime cache."""

from __future__ import annotations

import os
import time
from pathlib import Path

from backend.tools import verse_digests as vd

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
