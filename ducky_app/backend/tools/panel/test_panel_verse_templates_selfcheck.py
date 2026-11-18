"""Custom Verse templates: save single + pack, get, update, refuse Store ids, delete.

Was a hand-run block in panel_verse_templates.py that overwrote LOCALAPPDATA.
"""

from __future__ import annotations

import json

from backend.tools.panel import panel_verse_templates as vt


def test_custom_template_round_trip(tmp_path, monkeypatch):
    for var in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(tmp_path))

    single = vt.save_user_verse_template("Solo", content="using { /Verse.org/Simulation }\n")
    assert single.get("ok"), single
    tid = single["template"]["id"]
    assert tid.startswith("custom:")
    got = vt.get_user_verse_template(tid)
    assert got.get("ok") and "Simulation" in got["template"]["content"]

    pack = vt.save_user_verse_template(
        "PlayerCore",
        folder="PlayerCore",
        files_json=json.dumps([
            {"path": "player_api.verse", "content": "# api\n"},
            {"path": "impl/player.verse", "content": "# impl\n"},
        ]),
    )
    assert pack.get("ok"), pack
    assert len(pack["template"]["files"]) == 2

    updated = vt.save_user_verse_template("Solo2", content="# edited\n", template_id=tid)
    assert updated.get("ok") and updated["template"]["name"] == "Solo2"
    assert not vt.save_user_verse_template("X", template_id="plugin:verse:foo").get("ok")
    assert vt.list_user_verse_templates()["count"] == 2

    assert not vt.delete_user_verse_template(tid).get("ok")  # needs confirm
    assert vt.delete_user_verse_template(tid, confirm=True).get("ok")
    assert not vt.get_user_verse_template(tid).get("ok")
    assert (tmp_path / "UEFN-Ducky" / "verse_templates").is_dir()  # wrote under tmp, not real AppData
