"""panel_chats.local: the chats made before signing in, for the sidebar."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from backend.store import sealed_files
from backend.store.repos import chats
from backend.uefn_plugins import data_crypto
from frontend.ui_web.chats import folders, store
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import local

API = PanelApiChatsMixin()


def _repo(monkeypatch, **functions) -> None:
    monkeypatch.setattr(local, "_repo", lambda: SimpleNamespace(**functions))


def test_listing_never_fails_the_sidebar(monkeypatch):
    rows = [{"id": "c", "title": "T", "updated": 1.0, "chats": 1, "group": False}]
    _repo(monkeypatch, waiting=lambda: rows)
    assert API.list_local_chats() == {"ok": True, "chats": rows}

    def broken():
        raise OSError("database is locked")

    _repo(monkeypatch, waiting=broken)
    assert API.list_local_chats() == {"ok": True, "chats": []}
    monkeypatch.setattr(local, "_repo", lambda: None)  # chats kept as files: no accounts
    assert API.list_local_chats() == {"ok": True, "chats": []}
    assert API.adopt_local_chats(["c"]) == {"ok": True, "moved": [], "failed": []}
    assert API.delete_local_chats(["c"]) == {"ok": True, "deleted": []}


def test_adding_opens_the_files_and_reloads_every_sidebar(monkeypatch, tmp_path):
    asked: list = []
    opened: list = []
    told: list = []
    _repo(monkeypatch, adopt=lambda ids: asked.append(ids) or {"moved": ["a", "b"], "failed": ["c"]})
    monkeypatch.setattr(store, "_chats_root", lambda: tmp_path)
    monkeypatch.setattr(chats, "conv_project_id", lambda cid: store.ONE_LIST_SLUG if cid == "a" else "old-island")
    monkeypatch.setattr(sealed_files, "open_local_folder", lambda folder: opened.append(folder.relative_to(tmp_path).as_posix()))
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: told.append(kw))
    monkeypatch.setattr(folders, "_one_list_key", lambda: "this-account")
    folders._one_list_done.add("this-account")

    assert API.adopt_local_chats(["a", "", "c"]) == {"ok": True, "moved": ["a", "b"], "failed": ["c"]}
    assert asked == [["a", "c"]]
    assert opened == [
        "_no_project/conversations/a/attachments",
        "old-island/conversations/b/attachments",
        "_no_project/conversations/b/attachments",
    ]
    assert told == [{"open_tab": False}]
    # One was still stored under an island from before the one list: the next load moves it in.
    assert "this-account" not in folders._one_list_done

    API.adopt_local_chats(None)
    assert asked[-1] is None, "None means every one"


def test_adding_says_why_when_nothing_could_move(monkeypatch):
    told: list = []
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: told.append(kw))

    def locked(ids):
        raise data_crypto.Locked("no key")

    _repo(monkeypatch, adopt=locked)
    out = API.adopt_local_chats(["a"])
    assert out["ok"] is False and "data key" in out["error"] and out["moved"] == []
    _repo(monkeypatch, adopt=lambda ids: {"moved": [], "failed": ["a"]})
    assert API.adopt_local_chats(["a"]) == {"ok": False, "moved": [], "failed": ["a"]}
    assert told == []


def test_deleting_removes_the_chat_folders_and_tells_every_window(monkeypatch, tmp_path):
    told: list = []
    _repo(monkeypatch, remove=lambda ids: [("a", "old-island"), ("b", store.ONE_LIST_SLUG)])
    monkeypatch.setattr(store, "_chats_root", lambda: tmp_path)
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: told.append(kw))
    for project, conv_id in (("old-island", "a"), (store.ONE_LIST_SLUG, "b"), (store.ONE_LIST_SLUG, "other")):
        folder = tmp_path / project / "conversations" / conv_id / "attachments"
        folder.mkdir(parents=True)
        (folder / "shot.png").write_bytes(b"x")

    assert API.delete_local_chats(["a"]) == {"ok": True, "deleted": ["a", "b"]}
    assert not (tmp_path / "old-island" / "conversations" / "a").exists()
    assert not (tmp_path / store.ONE_LIST_SLUG / "conversations" / "b").exists()
    assert (tmp_path / store.ONE_LIST_SLUG / "conversations" / "other" / "attachments" / "shot.png").exists()
    assert told == [{"open_tab": False, "removed_conv_ids": ["a", "b"]}]
