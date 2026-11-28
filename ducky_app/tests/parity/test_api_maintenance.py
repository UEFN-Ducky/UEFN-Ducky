"""Settings → App Data: both backends list the same data folder the same way, refuse the
same paths and delete exactly the same files. Every folder here is the test's own; a
delete is run by each backend on a folder filled the same way, and the trees left are
compared."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from .parity_harness import plain

DAY = 86400
TUNNEL_KEY = "uefn-ducky/remote_named_tunnel"


def _put(base: Path, rel: str, size: int = 1, age_days: float = 0.0) -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        os.utime(path, (time.time() - age_days * DAY,) * 2)


def _fill(base: Path) -> None:
    for rel, size in (
        ("tool_captures/a.png", 40_000), ("tool_captures/deep/er/b.png", 5), ("tool_spills/s.json", 300), ("generated_images/g.png", 7),
        ("scratch.bin", 9), ("Odd Name/Über.txt", 3), ("chats/projects/Island_1/conversations/c1/attachments/pic.png", 2000),
        ("file_history/Island_1/v.verse", 10), ("memory/projects/Island_1/note.md", 4), ("credentials.dat", 6), ("panel.pid", 2),
        ("config.json", 2), ("listener/boot.py", 8), ("scopes/_pc/plug/folders/data/file.bin", 50), ("uefn_plugins/plug/plugin.json", 20),
        ("uefn_plugins/unity-mcp/plugin.json", 1), ("unity_mcp/run.bin", 3), ("verse-lsp/cache.bin", 11), ("legacy/old.json", 5),
        ("pyinstaller-work/b/x.o", 1), ("coding_agents/tmp/new.txt", 1), ("backups/copied/fresh.bin", 2), ("bin/cloudflared.exe", 4),
        ("diagnostics/cloudflared.log", 4),
    ):
        _put(base, rel, size)
    # Thirteen copies of one file, each a day older: the newest ten of the last 30 days stay.
    for n in range(12):
        _put(base, f"backups/panel_settings.json.bak.{n:02d}", 2, age_days=n + 0.5)
    for rel in ("backups/panel_settings.json.bak.old", "backups/copied/tree/data.bin"):
        _put(base, rel, 2, age_days=40)
    _put(base, "coding_agents/tmp/old.txt", 1, age_days=2)
    for empty in ("chats/projects/Empty_2", "chats/projects/Has_chat_3/conversations", "workspace/projects/Empty_2/x/y", "perf/a/b", "tasks",
                  "changesets/Empty_2"):
        (base / empty).mkdir(parents=True, exist_ok=True)


def _tree(base: Path) -> list[str]:
    """Every file and folder but the database's own."""
    out: list[str] = []
    for folder, dirs, files in os.walk(base):
        rel = Path(folder).relative_to(base).as_posix()
        if rel == "snapshots":
            dirs[:] = []
            continue
        out += [f"{rel}/{name}" for name in dirs + files if not name.startswith("ducky.db")]
    return sorted(out)


@pytest.fixture
def data(rust_store, python, tmp_path, monkeypatch):
    """``data.base`` is the data folder both backends use; ``data.reset()`` empties it (but
    for ducky.db) and fills it again; ``data.both(method, *args)`` asks both backends."""
    from backend.store.importers.boot import ensure_all_stores
    from frontend import appdata_maintenance, updater

    # That sweep also stops the programs of an older install: never from a test.
    monkeypatch.setattr(updater, "sweep_installer_cache", lambda **_kw: 0)
    ensure_all_stores()  # a new install has nothing to import: later files are no old stores

    class Data:
        base = appdata_maintenance.default_app_data_dir()
        calls = (python.call, rust_store.call)

        @classmethod
        def reset(cls) -> None:
            for child in cls.base.iterdir():
                if child.name.startswith("ducky.db"):
                    continue
                if os.path.isjunction(child):
                    os.rmdir(child)
                elif child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            _fill(cls.base)

        @classmethod
        def both(cls, method: str, *args: Any) -> Any:
            theirs, ours = (_steady(call(method, *args)) for call in cls.calls)
            assert ours == theirs, f"{method}{args}: rust {ours} != python {theirs}"
            assert theirs[0] == 200 and theirs[1]["ok"] is True, theirs
            return theirs[1]["result"]

    assert tmp_path in Data.base.parents
    return Data


def _steady(answer: Any) -> Any:
    """An answer without what changes by itself: the sizes of the database's files."""
    answer = plain(answer)
    result = answer[1].get("result")
    if isinstance(result, dict) and "items" in result:
        own = [row for row in result["items"] if row["name"].startswith("ducky.db")]
        result["items"] = [row for row in result["items"] if row not in own]
        result["database"] = sorted(({**row, "bytes": 0} for row in own), key=lambda row: row["name"])
        if "bytes" in result:
            result["bytes"] -= sum(row["bytes"] for row in own)
    return answer


def test_the_folder_is_listed_the_same(data) -> None:
    data.reset()
    for n in range(85):
        _put(data.base, f"many/{n:03d}.txt", n % 7)
    overview = data.both("appdata_overview")
    rows = {row["name"]: row for row in overview["items"]}
    assert rows["tool_captures"] == {
        "name": "tool_captures", "rel": "tool_captures", "is_dir": True, "bytes": 40_005, "files": 2, "dirs": 2, "kind": "cache",
        "label": "Captures", "description": "Screenshots and snips from tools.", "clearable": True, "deletable": True, "protected": False,
    }
    assert [row["name"] for row in overview["items"]][:2] == ["tool_captures", "chats"]
    assert rows["credentials.dat"]["protected"] and rows["listener"]["protected"] and rows["scopes"]["protected"] and rows["panel.pid"]["protected"]
    assert rows["Odd Name"]["kind"] == "other" and rows["Odd Name"]["deletable"] and rows["scratch.bin"]["files"] == 1
    assert next(row for row in overview["database"] if row["name"] == "ducky.db")["protected"]
    assert overview["bytes"] == sum(row["bytes"] for row in overview["items"]) and overview["root"] == str(data.base.resolve())

    assert [row["name"] for row in data.both("appdata_children", "tool_captures")["items"]] == ["a.png", "deep"]
    assert data.both("appdata_children", "\\chats/projects/")["rel"] == "chats/projects"
    assert data.both("appdata_children", "scopes")["items"][0]["label"] == "Local folder copies"
    copies = data.both("appdata_children", "scopes/_pc/plug")["items"][0]
    assert (copies["label"], copies["protected"], copies["bytes"]) == ("Folder copies", True, 50)
    many = data.both("appdata_children", "many")
    assert (len(many["items"]), many["total"], many["truncated"]) == (80, 85, True)
    assert data.both("appdata_children", "")["rel"] == data.both("appdata_children", None)["rel"] == ""
    for rel in ("backups", "Odd Name", "uefn_plugins"):
        assert data.both("appdata_children", rel)["total"] > 0
    for rel in ("nope", "..", "tool_captures/..", "tool_captures/.", "scratch.bin", "tool_captures//deep", str(data.base.parent)):
        assert data.both("appdata_children", rel) == {"rel": rel, "items": [], "database": [], "truncated": False, "total": 0, "error": "not_found"}


def test_clears_and_deletes_remove_the_same_files(data, tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("stays", encoding="utf-8")
    steps = [
        ("appdata_clear", "tool_captures"), ("appdata_clear", "tool_spills/s.json"), ("appdata_delete", "scratch.bin"),
        ("appdata_delete", "generated_images"), ("appdata_delete", "Odd Name/"), ("appdata_delete", "file_history\\Island_1"),
        # Never the settings, the plugins' data, the listener or the database.
        ("appdata_clear", "credentials.dat"), ("appdata_delete", "snapshots"), ("appdata_delete", "ducky.db"), ("appdata_clear", "scopes/_pc"),
        ("appdata_delete", "listener/boot.py"), ("appdata_delete", "config.json"),
        # Never the folder itself, nor anything outside it.
        ("appdata_clear", ""), ("appdata_delete", "/"), ("appdata_clear", None), ("appdata_delete", "../outside.txt"), ("appdata_delete", "chats/.."),
        ("appdata_delete", "chats/."), ("appdata_clear", "."), ("appdata_clear", "chats/.. "), ("appdata_delete", "chats/..."),
        ("appdata_delete", "chats//projects"), ("appdata_delete", str(outside)), ("appdata_delete", "nope"),
        ("appdata_clear_caches",),
    ]
    answers, trees = [], []
    for call in data.calls:
        data.reset()
        answers.append([plain(call(*step)) for step in steps])
        trees.append(_tree(data.base))
    assert answers[1] == answers[0] and trees[1] == trees[0]
    assert outside.read_text(encoding="utf-8") == "stays"
    said = [answer[1]["result"] for answer in answers[0]]
    assert said[:6] == [{"ok": True, "removed": 2}, {"ok": True, "removed": 1}, {"ok": True, "removed": 1}, {"ok": True, "removed": 1, "error": None},
                        {"ok": True, "removed": 1, "error": None}, {"ok": True, "removed": 1, "error": None}]
    assert all(answer == {"ok": False, "error": "protected", "removed": 0} for answer in said[6:12])
    assert all(answer == {"ok": False, "error": "refused_root", "removed": 0} for answer in said[12:15])
    assert all(answer == {"ok": False, "error": "not_found", "removed": 0} for answer in said[15:24])
    assert said[24]["ok"] is True and {"tool_captures", "legacy", "backups", "verse-lsp", "coding_agents"} <= set(said[24]["cleared"])
    left = trees[0]
    for kept in ("./tool_captures", "./credentials.dat", "listener/boot.py", "scopes/_pc/plug/folders/data/file.bin", "./config.json",
                 "chats/projects/Island_1/conversations/c1/attachments/pic.png", "uefn_plugins/plug/plugin.json", "./legacy", "./backups"):
        assert kept in left, kept
    for gone in ("tool_captures/a.png", "./scratch.bin", "./generated_images", "./Odd Name", "file_history/Island_1", "legacy/old.json", "verse-lsp/cache.bin"):
        assert gone not in left, gone
    assert not any(name.startswith("backups/") for name in left)


def _link_steps(data: Any, link: Path, make: Any) -> list[Any]:
    """What each backend lists and answers around a link, and what is left each time."""
    seen = []
    for call in data.calls:
        data.reset()
        log: list[Any] = []
        for step in (("appdata_children", "uefn_plugins/plug"), ("appdata_clear", "uefn_plugins/plug"), ("appdata_delete", "uefn_plugins/plug/data"),
                     ("appdata_clear", "uefn_plugins"), ("appdata_delete", "uefn_plugins")):
            if not os.path.lexists(link):
                link.parent.mkdir(parents=True, exist_ok=True)
                make()
            if step[0] == "appdata_children":
                log.append(_steady(data.calls[0](*step)) == _steady(data.calls[1](*step)))
            log += [_steady(call("appdata_overview")), plain(call(*step)), _tree(data.base)]
        seen.append(log)
    return seen


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows link")
def test_a_plugin_folders_junction_goes_and_its_copy_stays(data) -> None:
    import _winapi

    link, copy = data.base / "uefn_plugins/plug/data", data.base / "scopes/_pc/plug/folders/data"
    theirs, ours = _link_steps(data, link, lambda: _winapi.CreateJunction(str(copy), str(link)))
    assert ours == theirs
    assert theirs[0] is True and (copy / "file.bin").is_file() and not link.exists()
    # Listed as the link it is; cleared and deleted as the link, with its folder and with all plugins.
    row = next(row for row in data.calls[1]("appdata_overview")[1]["result"]["items"] if row["name"] == "scopes")
    assert row["bytes"] == 50
    assert [answer[1]["result"]["removed"] for answer in theirs[5::3]] == [2, 1, 2, 1]


def test_a_link_out_of_the_folder_is_removed_and_never_followed(data, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.txt").write_text("stays", encoding="utf-8")
    link = data.base / "uefn_plugins/plug/data"
    try:
        os.symlink(outside, data.base / "probe", target_is_directory=True)
    except OSError:
        pytest.skip("this account may not make symlinks")
    theirs, ours = _link_steps(data, link, lambda: os.symlink(outside, link, target_is_directory=True))
    assert ours == theirs
    assert theirs[0] is True and (outside / "theirs.txt").read_text(encoding="utf-8") == "stays" and not os.path.lexists(link)


def test_a_projects_data_is_listed_and_deleted_the_same(data, tmp_path) -> None:
    from backend.store.repos import chats, memory, projects
    from frontend.settings import PanelSettings

    data.reset()
    slugs = {}
    for name in ("Alpha", "Beta", "Gamma"):
        folder = tmp_path / "Projects" / name
        folder.mkdir(parents=True)
        slug = slugs[name] = projects.touch(str(folder), name=name, kind="folder")
        for n in range(2):
            chats.conv_save(slug, {"id": f"{name}-{n}", "title": name, "folder_id": "", "created": 1.0, "updated": 2.0, "sort_order": 1.0}, messages=None)
        memory.put(slug, "note", description="d", author="a", updated="2026-11-28", body="b")
        _put(data.base, f"chats/projects/{slug}/conversations/{name}-0/attachments/pic.png", 100 if name == "Gamma" else 30)
        _put(data.base, f"file_history/{slug}/v.verse", 5)
    memory.put("rows_only", "note", description="d", author="a", updated="2026-11-28", body="b")
    settings = PanelSettings.load()
    settings.uefn_project_root = str(tmp_path / "Projects" / "Gamma")
    settings.save()

    listed = data.both("appdata_projects")
    by_slug = {row["slug"]: row for row in listed["projects"]}
    # Biggest first.
    assert [row["slug"] for row in listed["projects"]][:2] == ["Island_1", slugs["Gamma"]] and listed["bytes"] == sum(row["bytes"] for row in listed["projects"])
    gamma = by_slug[slugs["Gamma"]]
    assert (gamma["label"], gamma["bytes"], [area["name"] for area in gamma["areas"]]) == ("Gamma", 105, ["Chats", "File history"])
    assert gamma["rows"]["conversations"] == 2 and gamma["rows"]["memory_entries"] == 1 and gamma["path"].endswith("Gamma")
    # Folders nobody opened and rows without a folder are projects too, under their id.
    assert by_slug["Island_1"]["label"] == "Island_1" and by_slug["rows_only"]["areas"] == [] and "Empty_2" in by_slug

    before = _tree(data.base)
    for wrong in ("..", ".", "C:", "a/b", "a\\b", "", None, " ", ".. ", "chats/projects"):
        assert data.both("appdata_delete_project", wrong) == {"ok": True, "removed": 0}
    assert _tree(data.base) == before
    theirs = plain(data.calls[0]("appdata_delete_project", slugs["Alpha"]))
    ours = plain(data.calls[1]("appdata_delete_project", slugs["Beta"]))
    # Two folders, two chats, a note and the project's own row.
    assert ours == theirs == [200, {"ok": True, "result": {"ok": True, "removed": 6}}]
    after = {row["slug"]: row for row in data.both("appdata_projects")["projects"]}
    assert slugs["Alpha"] not in after and slugs["Beta"] not in after and after[slugs["Gamma"]] == gamma
    assert not (data.base / "chats/projects" / slugs["Beta"]).exists() and not (data.base / "file_history" / slugs["Alpha"]).exists()
    assert chats.conv_count(slugs["Alpha"]) == chats.conv_count(slugs["Beta"]) == 0 and chats.conv_count(slugs["Gamma"]) == 2


def _new_store(base: Path) -> None:
    """As before any sweep: no copy, no check on record, and the old stores seen on two starts."""
    from backend.store import db

    shutil.rmtree(base / "snapshots", ignore_errors=True)
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM meta WHERE key IN ('last_integrity', 'last_full_integrity', 'snapshot_fingerprint')")
        conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('clean_boots', '2', 0)")


def _meta(key: str) -> Any:
    from backend.store import db

    row = db.connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def _age_copy(base: Path, days: float) -> Path:
    (copy,) = (base / "snapshots").glob("ducky-*.db")
    os.utime(copy, (time.time() - days * DAY,) * 2)
    return copy


def test_the_sweep_removes_the_same_and_each_backend_trusts_the_others_copy(data) -> None:
    from backend.agent import secrets
    from backend.store import db
    from backend.store.repos import chats
    from backend.store.repos import secrets as secret_rows

    chat = {"title": "c", "folder_id": "", "created": 1.0, "updated": 2.0, "sort_order": 1.0}
    chats.conv_save("Has_chat_3", {"id": "kept", **chat}, messages=None)
    results, trees = [], []
    for call in data.calls:
        data.reset()
        _new_store(data.base)
        secrets.set_key(TUNNEL_KEY, "token")
        status, reply = plain(call("appdata_sweep"))
        assert status == 200 and reply["ok"] is True, reply
        results.append(reply["result"])
        trees.append(_tree(data.base))
        assert TUNNEL_KEY not in secret_rows.all_blobs()
        assert len(list((data.base / "snapshots").glob("ducky-*-daily.db"))) == 1
        assert _meta("clean_boots") == "3" and '"result": "ok"' in _meta("last_integrity") and float(_meta("last_full_integrity")) > 1e9
    theirs, ours = results
    assert trees[1] == trees[0]
    assert ours == theirs and list(ours) == list(theirs)
    # Counted folders: pyinstaller-work, the day-old prompt file, the runtime that moved and backups/copied/tree.
    # The empty folders of the old layout go before the count.
    assert theirs == {
        "moved_backups": 0, "pruned_backups": 4, "removed_project_dirs": 4, "removed_installer_cache": 0, "removed_cloudflare_tunnel": 2,
        "db_checked": 1, "db_snapshot": 1, "db_imported": 0, "legacy_removed": 1,
    }
    left = trees[0]
    for kept in ("chats/projects/Island_1/conversations/c1/attachments/pic.png", "coding_agents/tmp/new.txt", "backups/copied/fresh.bin",
                 "backups/panel_settings.json.bak.09", "uefn_plugins/unity-mcp/runtime/run.bin", "./bin", "./tool_captures", "./scratch.bin"):
        assert kept in left, kept
    for gone in ("./legacy", "./pyinstaller-work", "./unity_mcp", "bin/cloudflared.exe", "diagnostics/cloudflared.log", "coding_agents/tmp/old.txt",
                 "backups/panel_settings.json.bak.10", "backups/panel_settings.json.bak.old", "backups/copied/tree", "./perf", "./tasks", "./workspace",
                 "./changesets", "chats/projects/Empty_2", "chats/projects/Has_chat_3"):
        assert gone not in left, gone

    # A copy Rust makes (the one above was taken before the tunnel's token went): Python sees its own
    # fingerprint in it, and neither copies unchanged data again.
    python, rust = data.calls
    shutil.rmtree(data.base / "snapshots")
    assert rust("appdata_sweep")[1]["result"]["db_snapshot"] == 1
    assert db.unchanged_since_snapshot(db.connect(), db.newest_snapshot()) is True
    assert rust("appdata_sweep")[1]["result"]["db_snapshot"] == 0  # the copy is today's
    for call in (python, rust):
        _age_copy(data.base, 2)
        again = call("appdata_sweep")[1]["result"]
        assert (again["db_snapshot"], again.get("db_snapshot_skipped")) == (0, 1), again
    # And the other way round: a copy Python made is current for Rust.
    shutil.rmtree(data.base / "snapshots")
    assert python("appdata_sweep")[1]["result"]["db_snapshot"] == 1
    _age_copy(data.base, 2)
    assert rust("appdata_sweep")[1]["result"].get("db_snapshot_skipped") == 1
    # Changed data, or a week: a new copy, and the newest three are kept.
    chats.conv_save("Has_chat_3", {"id": "new", **chat}, messages=None)
    changed = rust("appdata_sweep")[1]["result"]
    assert (changed["db_snapshot"], changed.get("db_snapshot_skipped")) == (1, None), changed
    assert db.unchanged_since_snapshot(db.connect(), db.newest_snapshot()) is True
    for copy in (data.base / "snapshots").glob("ducky-*.db"):
        os.utime(copy, (time.time() - 8 * DAY,) * 2)
    assert rust("appdata_sweep")[1]["result"]["db_snapshot"] == 1
    assert len(list((data.base / "snapshots").glob("ducky-*.db"))) == 3
