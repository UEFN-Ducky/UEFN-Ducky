"""LICENSE.txt beside an AppData skill pack is written only when its text changes."""
from pathlib import Path

from backend.skills import store


def test_unchanged_license_is_not_rewritten(tmp_path: Path, monkeypatch) -> None:
    # Every skill ship rewrote it: a disk write and an antivirus scan per pack, every 5 s
    # while a remote panel polled the listener.
    monkeypatch.setattr(store, "appdata_skill_packs_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "load_pack_manifest", lambda pid: {"id": pid, "label": "Ducky", "license": "MIT"})
    pack = tmp_path / "ducky"
    pack.mkdir()
    (pack / store.PACK_FILE).write_text("---\nname: ducky\n---\n", encoding="utf-8")

    store._write_pack_license_file("ducky")
    license_file = pack / store.LICENSE_FILE
    first = license_file.stat().st_mtime_ns
    text = license_file.read_text(encoding="utf-8")
    assert text

    store._write_pack_license_file("ducky")
    assert license_file.stat().st_mtime_ns == first

    monkeypatch.setattr(store, "load_pack_manifest", lambda pid: {"id": pid, "label": "Ducky", "license": "Apache-2.0"})
    store._write_pack_license_file("ducky")
    assert license_file.read_text(encoding="utf-8") != text
