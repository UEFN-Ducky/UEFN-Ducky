"""ducky_find (backend/search/find.py): the person's own things in every plugin, by name,
only in copies they can open. Oct 26 2026: an agent offered to make a picture for a promo
video that sat in Island Media Kit's team copy, because nothing could find it by name."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.search import find
from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import host, scopes
from backend.uefn_plugins.scopes import PluginData
from backend.uefn_plugins.test_scopes import FakeSpaces, _Who, account_keys, fake_spaces, open_space, who  # noqa: F401

IMK = "island-media-kit"


@pytest.fixture
def plugins(monkeypatch):
    on = {IMK, "forge"}
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: pid in on)
    monkeypatch.setattr(host, "_plugin_label", lambda pid: {IMK: "Island Media Kit", "forge": "Ducky Forge"}.get(pid, pid))
    monkeypatch.setattr(host, "_PLUGIN_TOOL_OWNER", {"imk_project_get": IMK, "imk_project_list": IMK, "imk_media_list": IMK})
    return on


def test_score_and_open_with() -> None:
    assert find.score("UEFN Ducky Promo", "project.1", "uefn ducky promo") == 3.0
    assert find.score("UEFN Ducky Promo", "project.1", "ducky promo") == 2.0
    assert find.score("UEFN Ducky Promo 30s", "project.1", "promo uefn video") == pytest.approx(2 / 3)
    assert find.score("Untitled island 1", "project.1", "promo video") == 0.0
    assert find.score("2026-09-17 22-44-13.mp4", "media.1", "promo mp4") == 0.0  # a file type alone
    assert find.score("promo_final.mp4", "media.2", "promo mp4") == 1.0
    tools = ["imk_media_list", "imk_project_get", "imk_project_list"]
    assert find.open_with(tools, "project.80a9") == "imk_project_get"
    assert find.open_with(tools, "media/clip.mp4") == "imk_media_list"
    assert find.open_with(tools, "card.pip") == ""


def test_finds_by_name_in_every_copy_the_person_can_open(who: _Who, fake_spaces: FakeSpaces, plugins) -> None:
    imk, forge = PluginData(IMK), PluginData("forge")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Mindful Path", plugin=IMK)
    imk.put("project.80a9", {"name": "UEFN Ducky Promo", "video": {"media": []}})
    imk.put("project.x", {"name": "Untitled island 1"})
    imk.put_file("exports/promo_final.mp4", b"mp4")
    scopes.link_plugin(IMK, scopes.PERSONAL)  # the plugin now shows Local; the team copy stays
    imk.put("project.l", {"name": "Local promo draft"})
    imk.put("key.k", {"name": "Promo key"}, sensitive=True)
    forge.put("scene.promo", {"title": "Promo stage"})
    PluginData("switched-off").put("project.p", {"name": "Promo elsewhere"})

    out = find.find("ducky promo")
    assert out["ok"] and out["hits"][0]["label"] == "UEFN Ducky Promo"
    top = out["hits"][0]
    assert (top["plugin"], top["copy"], top["key"], top["open_with"]) == (IMK, "Mindful Path", "project.80a9",
                                                                          "imk_project_get")
    assert not top["plugin_shows_this_copy"] and "ducky_plugin_data_scope" in top["note"]

    labels = {h["label"]: h for h in find.find("promo", limit=20)["hits"]}
    assert {"UEFN Ducky Promo", "Local promo draft", "Promo stage", "promo_final.mp4"} <= set(labels)
    assert "Promo key" not in labels and "Promo elsewhere" not in labels  # sensitive, plugin off
    assert labels["Local promo draft"]["plugin_shows_this_copy"] and "note" not in labels["Local promo draft"]
    assert labels["promo_final.mp4"]["kind"] == "file"
    assert find.find("promo", plugin="forge")["hits"][0]["label"] == "Promo stage"
    assert find.find("ducky promo")["searched"] == {"Ducky Forge": ["Local"],
                                                    "Island Media Kit": ["Local", "Mindful Path"]}

    # Access to the team is lost: its copy is never searched (nor shown) again.
    repo.sync_put(ana, "spaceT", state="lost")
    assert all(h["copy"] != "Mindful Path" for h in find.find("promo", limit=20)["hits"])


def test_a_hit_names_its_file_so_the_chat_card_plays_it(who: _Who, plugins, tmp_path) -> None:
    who.be("ana@x.org")
    video = tmp_path / "UEFN Ducky Promo - Trailer 1 - 1080p.mp4"
    video.write_bytes(b"mp4")
    data = PluginData(IMK)
    data.put("export.file.j1", {"name": video.name, "path": str(video)})
    data.put("export.file.j2", {"name": "Old promo.mp4", "path": str(tmp_path / "gone.mp4")})
    hits = {h["label"]: h for h in find.find("promo", limit=10)["hits"]}
    assert hits[video.name]["path"] == str(video)
    assert "path" not in hits["Old promo.mp4"]  # a file that is gone is never offered


def test_nothing_found_says_so_and_never_suggests_making_it(who: _Who, plugins) -> None:
    who.be("ana@x.org")
    PluginData(IMK).put("project.x", {"name": "Untitled island 1"})
    out = find.find("dragon boss trailer")
    assert out["ok"] and out["hits"] == [] and "never make or pay" in out["hint"]
    assert find.find("  ")["ok"] is False
