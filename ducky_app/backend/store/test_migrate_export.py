"""Move to Linux: the archive opens only with its passphrase, and an export then an import on
another PC (a fresh AppData) brings back chats, settings, plans, workflows and keys without
overwriting what that PC already has."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from backend.agent import secrets
from backend.automations import store as wf_store
from backend.store import migrate_export as mx
from backend.store.repos import chats, plans, settings

PASS = "correct horse battery"
PROJECT = "proj_move"


def _fill() -> None:
    chats.conv_save(PROJECT, {"id": "c1", "title": "Goblin arena", "created": 1.0, "updated": 2.0, "pinned": True},
                    messages=[{"role": "user", "text": "hello goblins", "content": "hello goblins", "ts": 1.0}])
    settings.save_fields({"theme": "dark"}, {})
    plans.plan_put("_no_project", "c1", "project", {"id": "p1", "title": "Arena plan", "status": "open"})
    wf_store.save_workflow({"id": "w1", "name": "Nightly", "graph": {"nodes": [], "edges": []}})
    secrets.save_keys({"anthropic": "sk-test-123"})


def _new_pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Another PC: an empty AppData root and no process caches."""
    import conftest

    root = tmp_path / name
    (root / "Roaming").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    monkeypatch.setenv("APPDATA", str(root / "Roaming"))
    conftest._reset_store_memos()
    secrets.clear_memory_cache()


def test_archive_opens_with_passphrase_only(tmp_path):
    _fill()
    out = mx.export_archive(tmp_path, PASS)
    path = Path(out["path"])
    assert path.name.startswith("ducky-move-") and path.suffix == ".zip"
    manifest = mx.read_manifest(path)
    assert manifest["format"] == mx.FORMAT and manifest["os"] and manifest["app_version"]
    assert out["counts"]["chats"] == 1 and out["counts"]["keys"] == 1
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("payload.bin")
    assert b"sk-test-123" not in raw and b"Goblin" not in raw
    _, data = mx.open_archive(path, PASS)
    assert data["keys"] == {"anthropic": "sk-test-123"}
    assert data["chats"][0]["doc"]["title"] == "Goblin arena"
    with pytest.raises(mx.MoveError, match="Wrong passphrase"):
        mx.open_archive(path, "not the passphrase")
    with pytest.raises(mx.MoveError):
        mx.export_archive(tmp_path, "short")


def test_edited_manifest_is_refused(tmp_path):
    _fill()
    path = Path(mx.export_archive(tmp_path, PASS)["path"])
    with zipfile.ZipFile(path) as zf:
        head, body = json.loads(zf.read("manifest.json")), zf.read("payload.bin")
    head["os"] = "elsewhere"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(head))
        zf.writestr("payload.bin", body)
    with pytest.raises(mx.MoveError):
        mx.open_archive(path, PASS)


def test_not_an_archive(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(mx.MoveError, match="isn't a Ducky move archive"):
        mx.read_manifest(bad)


def test_round_trip_to_another_pc(tmp_path, monkeypatch):
    _fill()
    path = Path(mx.export_archive(tmp_path, PASS)["path"])
    _new_pc(tmp_path, monkeypatch, "linux-pc")
    with pytest.raises(mx.MoveError):
        mx.import_archive(path, "wrong passphrase!")
    assert chats.conv_get("c1") is None  # nothing written by a failed import

    result = mx.import_archive(path, PASS)
    assert result["imported"]["chats"] == 1 and result["imported"]["keys"] == 1
    assert result["summary"] == "1 chats, 1 keys imported, 0 skipped"
    doc = chats.conv_get("c1")
    assert doc["title"] == "Goblin arena" and doc["pinned"] is True
    assert doc["messages"][0]["text"] == "hello goblins"
    assert settings.load_fields()["theme"] == "dark"
    assert plans.plan_get("_no_project", "c1")["title"] == "Arena plan"
    assert [w["name"] for w in wf_store.all_workflows()] == ["Nightly"]
    secrets.clear_memory_cache()
    assert secrets.load_keys(use_cache=False) == {"anthropic": "sk-test-123"}


def test_panel_route(tmp_path, monkeypatch):
    import frontend.ui_web.panel_api  # noqa: F401 — loads the mixins in their order
    from frontend.ui_web.panel_api_project import PanelApiProjectMixin

    api = PanelApiProjectMixin()
    _fill()
    out = api.move_to_linux("export", str(tmp_path), PASS)
    assert out["ok"] and out["counts"]["chats"] == 1
    _new_pc(tmp_path, monkeypatch, "linux-pc")
    bad = api.move_to_linux("import", out["path"], "wrong passphrase!")
    assert bad == {"ok": False, "error": "Wrong passphrase, or the archive was changed."}
    good = api.move_to_linux("import", out["path"], PASS)
    assert good["ok"] and good["summary"] == "1 chats, 1 keys imported, 0 skipped"
    assert api.move_to_linux("nope", "", "")["ok"] is False


def test_existing_chat_and_key_are_skipped(tmp_path, monkeypatch):
    _fill()
    path = Path(mx.export_archive(tmp_path, PASS)["path"])
    _new_pc(tmp_path, monkeypatch, "linux-pc")
    chats.conv_save(PROJECT, {"id": "c1", "title": "Mine", "created": 5.0, "updated": 5.0}, messages=[])
    secrets.save_keys({"anthropic": "sk-linux"})

    result = mx.import_archive(path, PASS)
    assert result["skipped"]["chats"] == 1 and result["skipped_chats"] == ["c1"]
    assert result["skipped"]["keys"] == 1 and result["imported"]["keys"] == 0
    assert chats.conv_get("c1")["title"] == "Mine"
    secrets.clear_memory_cache()
    assert secrets.load_keys(use_cache=False) == {"anthropic": "sk-linux"}


@pytest.mark.parametrize(("plat", "word"), [("darwin", "macos"), ("win32", "windows"), ("linux", "linux")])
def test_export_names_the_os_in_plain_words(monkeypatch, plat, word):
    monkeypatch.setattr(mx.sys, "platform", plat)
    assert mx._os_name() == word
