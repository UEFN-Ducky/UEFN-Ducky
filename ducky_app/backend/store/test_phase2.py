"""Phase 2 (conversations): repo parity on both backends, import, FTS, guards."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from backend.store import db
from backend.store.repos import chats as repo


@pytest.fixture(params=["db", "files"])
def backend(request, monkeypatch) -> str:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    return request.param


@pytest.fixture
def project(tmp_path: Path) -> str:
    root = tmp_path / "Island"
    (root / "Content").mkdir(parents=True)
    return str(root)


def _msg(role: str, text: str, **extra) -> dict:
    return {"role": role, "content": text, "text": text, "ts": time.time(), **extra}


# --------------------------------------------------------------------------- parity


def test_create_list_append_upsert_delete(backend: str, project: str) -> None:
    from frontend.ui_web import project_chats as pc

    conv = pc.create_conversation(folder_id="", title="Alpha", project_root=project, skill_snapshot="INDEX")
    other = pc.create_conversation(folder_id="", title="Beta", project_root=project, skill_snapshot="INDEX")
    assert other.sort_order == conv.sort_order + 1.0
    pc.append_message(conv, _msg("user", "hello duck"), project)
    pc.upsert_in_flight_assistant(conv, _msg("assistant", "partial", incomplete=True), run_id="r1", project_root=project)
    pc.upsert_in_flight_assistant(conv, _msg("assistant", "final answer"), run_id="r1", project_root=project)
    loaded = pc.load_conversation(conv.id, project)
    assert [m["role"] for m in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[1]["content"] == "final answer"
    assert loaded.skill_snapshot == "INDEX"
    listed = pc.list_all_conversation_metadata(project)
    assert [c.title for c in listed] == ["Alpha", "Beta"]
    assert all(c.messages == [] for c in listed)
    pc.delete_conversation(conv.id, project)
    assert pc.load_conversation(conv.id, project) is None
    assert [c.title for c in pc.list_all_conversation_metadata(project)] == ["Beta"]


def test_metadata_stub_never_blanks_messages(backend: str, project: str) -> None:
    from frontend.ui_web import project_chats as pc

    conv = pc.create_conversation(title="Stub", project_root=project, skill_snapshot="x")
    pc.append_message(conv, _msg("user", "q"), project)
    pc.append_message(conv, _msg("assistant", "a"), project)
    stub = pc.list_all_conversation_metadata(project)[0]
    stub.title = "Renamed"
    pc.save_conversation(stub, project)
    again = pc.load_conversation(conv.id, project)
    assert again.title == "Renamed"
    assert [m["role"] for m in again.messages] == ["user", "assistant"]
    # a user-only stub must not drop a checkpointed assistant either
    user_only = pc.load_conversation(conv.id, project)
    user_only.messages = [m for m in user_only.messages if m["role"] == "user"]
    pc.save_conversation(user_only, project)
    assert [m["role"] for m in pc.load_conversation(conv.id, project).messages] == ["user", "assistant"]


def test_sidebar_counts_are_maintained(backend: str, project: str) -> None:
    from frontend.ui_web import project_chats as pc

    conv = pc.create_conversation(title="Counts", project_root=project, skill_snapshot="x")
    block = {"type": "tool_call", "name": "workspace_write_file", "status": "success",
             "arguments": {"relative_path": "Content/Verse/a.verse", "content": "x"}}
    pc.append_message(conv, _msg("assistant", "wrote", blocks=[block, dict(block)]), project)
    row = pc.list_all_conversation_metadata(project)[0]
    assert row.tool_call_count == 2 and row.file_count == 1


def test_folders_round_trip_and_archive(backend: str, project: str) -> None:
    from frontend.ui_web import project_chats as pc

    folders = pc.load_folders(project)
    assert any(f.id == "archive" for f in folders)
    made = pc.create_folder("Work", "", project)
    pc.rename_folder(made.id, "Work2", project)
    names = {f.id: f.name for f in pc.load_folders(project)}
    assert names[made.id] == "Work2"
    conv = pc.create_conversation(title="In folder", project_root=project, skill_snapshot="x")
    pc.move_conversation(conv.id, made.id, project)
    assert pc.load_conversation(conv.id, project).folder_id == made.id
    assert pc.delete_folder(made.id, project) == []
    assert pc.load_conversation(conv.id, project).folder_id == ""


def test_descendants_and_cascade_delete(backend: str, project: str) -> None:
    from frontend.ui_web import project_chats as pc

    parent = pc.create_conversation(title="P", project_root=project, skill_snapshot="x")
    child = pc.create_conversation(title="C", project_root=project, skill_snapshot="x", parent_conv_id=parent.id)
    grand = pc.create_conversation(title="G", project_root=project, skill_snapshot="x", parent_conv_id=child.id)
    assert pc.conversation_descendant_ids(parent.id, project) == [child.id, grand.id]
    pc.delete_conversation(parent.id, project)
    assert pc.load_conversation(grand.id, project) is None


def test_conversation_from_other_project_is_invisible(backend: str, project: str, tmp_path: Path) -> None:
    from frontend.ui_web import project_chats as pc

    other = str(tmp_path / "Other")
    Path(other).mkdir()
    conv = pc.create_conversation(title="Mine", project_root=project, skill_snapshot="x")
    assert pc.load_conversation(conv.id, other) is None
    assert pc.list_all_conversation_metadata(other) == []


# --------------------------------------------------------------------------- db-only behaviour


def test_append_writes_one_row(project: str) -> None:
    from frontend.ui_web import project_chats as pc

    conv = pc.create_conversation(title="Rows", project_root=project, skill_snapshot="x")
    for i in range(5):
        pc.append_message(conv, _msg("user", f"m{i}"), project)
    stats = repo.conv_save(pc.project_slug(project), conv.to_dict(), messages=conv.messages + [_msg("user", "m5")])
    assert stats["inserted"] == 1 and stats["updated"] == 0 and stats["deleted"] == 0


def test_skill_snapshot_is_deduplicated(project: str) -> None:
    from frontend.ui_web import project_chats as pc

    big = "SKILL INDEX " * 2000
    for i in range(3):
        pc.create_conversation(title=f"S{i}", project_root=project, skill_snapshot=big)
    conn = db.connect()
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 3


def test_body_search_finds_messages(project: str, monkeypatch) -> None:
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats as pc
    from frontend.ui_web.workspace_search import search_workspace

    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = pc.create_conversation(title="Search me", project_root=project, skill_snapshot="x")
    pc.append_message(conv, _msg("user", "where is the goblin spawner wired"), project)
    pc.append_message(conv, _msg("assistant", "The goblin spawner is wired to trigger_1."), project)
    out = search_workspace("goblin spawner", scope="chats", max_results=50)
    hits = out["chat_results"]
    assert hits and hits[0]["id"] == conv.id
    ids = {m["message_id"] for m in hits[0]["matches"]}
    assert 0 in ids and 1 in ids


def test_import_from_legacy_chat_tree(tmp_path: Path, project: str) -> None:
    from backend.store.importers import phase2
    from frontend.chat_store import Conversation
    from frontend.ui_web import project_chats as pc

    root = tmp_path / ".ducky-appdata" / "UEFN-Ducky"
    slug = pc.project_slug(project)
    proj = root / "chats" / "projects" / slug
    (proj / "conversations" / "c1").mkdir(parents=True)
    (proj / "conversations" / "c1" / "attachments").mkdir()
    (proj / "conversations" / "c1" / "attachments" / "shot.png").write_bytes(b"png")
    (proj / "folders.json").write_text(json.dumps({"folders": [{"id": "f1", "name": "Old", "sort_order": 1}]}))
    doc = Conversation(id="c1", title="Legacy", created=1.0, updated=2.0, skill_snapshot="idx",
                       enabled_packs=None, disabled_packs=[], messages=[_msg("user", "hi"), _msg("assistant", "yo")]).to_dict()
    (proj / "conversations" / "c1" / "conversation.json").write_text(json.dumps(doc), encoding="utf-8")
    flat = root / "chats" / "conversations"
    flat.mkdir(parents=True)
    (flat / "old.json").write_text(json.dumps(Conversation(id="old", title="Flat").to_dict()), encoding="utf-8")

    loaded = pc.load_conversation("c1", project)
    assert loaded is not None and loaded.title == "Legacy" and len(loaded.messages) == 2
    assert loaded.disabled_packs == [] and loaded.enabled_packs is None  # tri-state survives
    assert {f.id for f in pc.load_folders(project)} >= {"f1", "archive"}
    rep = phase2.report()
    assert rep["conversations"] == 2 and rep["messages"] == 2 and rep["folders"] == 1
    assert not (proj / "conversations" / "c1" / "conversation.json").exists()
    assert (root / "legacy" / "chats" / "projects" / slug / "conversations" / "c1" / "conversation.json").exists()
    assert (proj / "conversations" / "c1" / "attachments" / "shot.png").exists()  # attachments stay
    assert repo.conv_get("old", project_id=phase2.LEGACY_FLAT_PROJECT) is not None
