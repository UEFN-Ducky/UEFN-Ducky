"""Teammates share plugin drafts: a stale whole-file write never erases another agent's edit."""

from __future__ import annotations

import pytest

from backend.tools.panel import panel_ai_plugins as p


@pytest.fixture
def draft(tmp_path, monkeypatch):
    root = tmp_path / "rn"
    (root / "backend").mkdir(parents=True)
    monkeypatch.setattr(p, "_draft_root", lambda pid: root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(p, "_seen", {})
    who = {"me": ("chat-a", "City Builder")}
    monkeypatch.setattr(p, "_me", lambda: who["me"])
    return root, who


def test_a_stale_whole_write_is_refused_and_names_who(draft) -> None:
    root, who = draft
    assert p.write_ai_plugin_file("rn", "backend/x.py", "a = 1\n")["ok"]
    assert p.read_ai_plugin_file("rn", "backend/x.py")["ok"]  # A has seen a = 1
    who["me"] = ("chat-b", "Freeway Ramps")
    assert p.edit_ai_plugin_file("rn", "backend/x.py", "a = 1", "a = 2")["ok"]  # B edits
    who["me"] = ("chat-a", "City Builder")
    out = p.write_ai_plugin_file("rn", "backend/x.py", "a = 1\nb = 1\n")  # A rewrites from its old copy
    assert not out["ok"] and out["stale"]
    assert out["changed_by"]["name"] == "Freeway Ramps" and "Freeway Ramps (chat chat-b)" in out["error"]
    assert (root / "backend" / "x.py").read_text(encoding="utf-8") == "a = 2\n"  # B's edit survives
    # Reading again makes the write legal; the read also says who is in the file.
    read = p.read_ai_plugin_file("rn", "backend/x.py")
    assert read["last_changed_by"]["name"] == "Freeway Ramps"
    assert p.write_ai_plugin_file("rn", "backend/x.py", "a = 2\nb = 1\n")["ok"]


def test_a_blind_overwrite_of_a_teammates_file_is_refused(draft) -> None:
    root, who = draft
    who["me"] = ("chat-b", "Freeway Ramps")
    assert p.write_ai_plugin_file("rn", "backend/y.py", "theirs = 1\n")["ok"]
    who["me"] = ("chat-a", "City Builder")
    out = p.write_ai_plugin_file("rn", "backend/y.py", "mine = 1\n")
    assert not out["ok"] and "have not read it" in out["error"]
    assert p.write_ai_plugin_file("rn", "backend/y.py", "mine = 1\n", expected_hash=out["hash"])["ok"]


def test_files_with_no_agent_writer_stay_writable(draft) -> None:
    root, _who = draft
    (root / "plugin.json").write_text('{"id": "rn"}\n', encoding="utf-8")  # scaffold / the person
    assert p.write_ai_plugin_file("rn", "plugin.json", '{"id": "rn", "label": "RN"}')["ok"]
    assert p.write_ai_plugin_file("rn", "backend/z.py", "z = 1\n")["ok"]
    (root / "backend" / "z.py").write_text("z = 9\n", encoding="utf-8")  # shell copy after our write
    out = p.write_ai_plugin_file("rn", "backend/z.py", "z = 2\n")
    assert not out["ok"] and "outside the plugin tools" in out["changed_by"]["name"]


def test_an_edit_lands_on_the_newest_text(draft) -> None:
    root, who = draft
    assert p.write_ai_plugin_file("rn", "backend/w.py", "a = 1\nb = 1\n")["ok"]
    who["me"] = ("chat-b", "Freeway Ramps")
    assert p.edit_ai_plugin_file("rn", "backend/w.py", "a = 1", "a = 2")["ok"]
    who["me"] = ("chat-a", "City Builder")
    assert p.edit_ai_plugin_file("rn", "backend/w.py", "b = 1", "b = 2")["ok"]  # edits never go stale
    assert (root / "backend" / "w.py").read_text(encoding="utf-8") == "a = 2\nb = 2\n"
