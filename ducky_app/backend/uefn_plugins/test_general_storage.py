"""General storage: notes, JSON and files under names and folders, kept like plugin data, so it
follows Your team (Local or a team copy, queued to sync with history) and every plugin and agent
can use it."""

from __future__ import annotations

import base64
import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import general_storage as gs
from backend.uefn_plugins import scopes, test_scopes
from backend.uefn_plugins.test_scopes import FakeSpaces, open_space

account_keys = test_scopes.account_keys
who = test_scopes.who
fake_spaces = test_scopes.fake_spaces


def test_notes_json_and_files_by_name_and_folder(who) -> None:
    who.be("ana@x.org")
    note = gs.put_value("Ideas", "Limo jumps the bridge", folder="Promo/2026")["item"]
    assert (note["kind"], note["folder"], note["by"]) == ("note", "promo/2026", "you")
    gs.put_value("Spawn table", {"cars": 3}, by="tycoon")
    pic = gs.put_file("Cover Art.PNG", b"PNG1", folder="Promo")["item"]
    assert (pic["path"], pic["size"], pic["mime"]) == ("promo/cover-art.png", 4, "image/png")
    assert [r["name"] for r in gs.items()] == ["Cover Art.PNG", "Spawn table", "Ideas"]
    assert [r["name"] for r in gs.items("promo")] == ["Cover Art.PNG", "Ideas"]
    assert [r["name"] for r in gs.items(query="spawn")] == ["Spawn table"]
    assert gs.get(note["id"])["text"] == "Limo jumps the bridge"
    assert gs.find("Spawn table")["value"] == {"cars": 3}
    assert gs.get(pic["id"], with_data=True)["data"] == b"PNG1"
    # The same name again replaces it (one item; its history keeps the old one).
    again = gs.put_file("Cover Art.PNG", b"PNG2", folder="Promo")["item"]
    assert again["id"] == pic["id"] and gs.get(pic["id"], with_data=True)["data"] == b"PNG2"
    assert len(gs.items("promo")) == 2
    assert gs.delete(pic["id"])["ok"] and gs.get(pic["id"]) is None
    assert not scopes.PluginData(gs.GENERAL).has_file("promo/cover-art.png")
    assert gs.delete(pic["id"])["ok"] is False
    with pytest.raises(ValueError):
        gs.put_value("", "x")


def test_it_follows_your_team_and_queues_for_sync(who, fake_spaces: FakeSpaces) -> None:
    aid = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha", plugin=gs.GENERAL)
    assert gs.where()["label"] == "Alpha"
    gs.put_file("clip.webm", b"WEBM", folder="Trailers")
    assert repo.count_dirty(aid, "spaceT", gs.GENERAL) == 2  # the file and its doc, one change
    assert repo.rows(aid, scopes.PERSONAL, gs.GENERAL, "doc") == []  # the team's, not Local's


def test_plugins_and_agents_use_the_same_storage(who) -> None:
    from backend.tools.panel import panel_general_storage as tools

    who.be("ana@x.org")
    gs.StorageApi("island-media-kit").put_file("render.mp4", b"MP4")
    out = json.loads(tools.ducky_storage_put("Notes", text="hi", folder="Team"))
    assert out["ok"] and out["item"]["by"] == "ducky"
    listed = json.loads(tools.ducky_storage_list())
    assert {(r["name"], r["by"]) for r in listed["items"]} == {("render.mp4", "island-media-kit"), ("Notes", "ducky")}
    assert json.loads(tools.ducky_storage_get(name="Notes", folder="Team"))["item"]["text"] == "hi"
    b64 = base64.b64encode(b"\x00\x01").decode()
    assert json.loads(tools.ducky_storage_put("bin.dat", b64=b64))["item"]["size"] == 2
    assert json.loads(tools.ducky_storage_put("bad", json_value="{nope"))["ok"] is False


def test_save_to_writes_project_files_through_the_pipeline(who, tmp_path, monkeypatch) -> None:
    from backend.tools.panel import panel_general_storage as tools
    from backend.workspace import runtime
    from backend.workspace.writer import ProjectWriter

    who.be("ana@x.org")
    project = tmp_path / "Island"
    (project / "Content").mkdir(parents=True)
    writer = ProjectWriter.for_root(str(project))
    monkeypatch.setattr(runtime, "get_writer", lambda: writer)
    item = gs.put_file("cover.png", b"PNG1", folder="Promo")["item"]
    out = json.loads(tools.ducky_storage_get(id=item["id"], save_to=str(project / "Content") + "/"))
    assert out["ok"] and (project / "Content" / "cover.png").read_bytes() == b"PNG1"
    outside = tmp_path / "Elsewhere" / "cover.png"
    assert json.loads(tools.ducky_storage_get(id=item["id"], save_to=str(outside)))["ok"] is False
    assert not outside.exists()
