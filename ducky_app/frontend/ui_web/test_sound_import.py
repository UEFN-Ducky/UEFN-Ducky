from __future__ import annotations

from frontend.ui_web import sound_import


def test_copies_audio_under_a_safe_unique_name(tmp_path):
    src = tmp_path / "my song!.MP3"
    src.write_bytes(b"ID3")
    out = sound_import.copy_into_sounds(str(src), tmp_path / "sounds")
    assert out["ok"] is True
    name = out["filename"]
    assert name.startswith("my_song__") and name.endswith(".mp3")
    assert (tmp_path / "sounds" / name).read_bytes() == b"ID3"


def test_refuses_cancel_missing_and_non_audio(tmp_path):
    sounds = tmp_path / "sounds"
    assert sound_import.copy_into_sounds(None, sounds) == {"ok": False, "error": "cancelled"}
    assert sound_import.copy_into_sounds(str(tmp_path / "nope.wav"), sounds)["error"] == "File not found"
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    assert sound_import.copy_into_sounds(str(txt), sounds)["error"] == "Unsupported audio type: .txt"
    assert not sounds.exists()


def test_import_falls_back_to_tk_and_reports_a_broken_dialog(tmp_path, monkeypatch):
    src = tmp_path / "ding.wav"
    src.write_bytes(b"RIFF")
    monkeypatch.setattr("backend.skills.discovery.appdata_dir", lambda: tmp_path)
    monkeypatch.setattr(sound_import, "_pick_with_tk", lambda: str(src))
    out = sound_import.import_sound_file(None)
    assert out["ok"] is True and (tmp_path / "sounds" / out["filename"]).is_file()

    def broken():
        raise RuntimeError("no display")

    monkeypatch.setattr(sound_import, "_pick_with_tk", broken)
    assert sound_import.import_sound_file(None) == {"ok": False, "error": "Could not open file dialog"}
