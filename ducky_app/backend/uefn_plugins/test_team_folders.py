"""Plugins that keep their own folders (``team_folders`` in plugin.json), on one PC with a
temp LOCALAPPDATA: adoption (rename, copy + verify), junctions re-pointed between Local
and a team and repaired, each refusal reason, the quiet rule, ordered apply with exact
mtimes, read-only units, a dirty unit never overwritten, {PROJECT} copies per project id,
UEFN revision control workspaces, call markers across processes, plugin tests, name
normalisation, long paths, the first share, links made before this PC kept team copies,
JSON merges, keep mine as a copy / discard mine, purge, and App Data's view of it.

Every folder, junction and LOCALAPPDATA here is under the test's temp dir; junctions are
removed with ``os.rmdir`` on the junction at the end of each test.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions and DPAPI")

from backend.store.repos import folder_files as repo
from backend.uefn_plugins import team_folders as tf

PLUGIN = "scene-maker"
SCENES = {
    "id": "scenes", "root": "{LOCALAPPDATA}/UEFN-Ducky/scene-maker/scenes", "unit": "*", "lock": "unit",
    "last": ["scene.json"], "ignore": ["*/history.json", "*/history-snapshots/**", "**/*.tmp"],
    "after_pull": "reload",
}
DESIGN = {
    "id": "design", "root": "{PROJECT}/.ducky/roguelike-design", "unit": ".", "lock": "unit",
    "last": ["store.json"], "ignore": ["store.prev.json", "**/*.tmp"], "after_pull": "reload",
}
GUID = "86EF2167-1111-2222-3333-444444444444"
MTIME = 1_789_000_000_123_456_700  # 100 ns ticks: NTFS keeps it exactly


def _drop_junctions(base: Path) -> None:
    """Remove every junction under ``base`` (``os.rmdir`` on the junction; never followed)."""
    if not base.is_dir():
        return
    stack = [str(base)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            if e.is_junction():
                os.rmdir(e.path)
            elif e.is_dir(follow_symlinks=False):
                stack.append(e.path)


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.tmp = tmp_path
        self.key = "https://uefnducky.org|ana@x.org"
        self.local = Path(os.environ["LOCALAPPDATA"])

    @property
    def account(self) -> str:
        from backend.uefn_plugins import scopes

        return scopes.account_id()

    def manifest(self, *folders: dict[str, Any], plugin: str = PLUGIN, **extra: Any) -> None:
        from backend.uefn_plugins.store import PLUGIN_MANIFEST, plugin_dir

        root = plugin_dir(plugin)
        root.mkdir(parents=True, exist_ok=True)
        path = root / PLUGIN_MANIFEST
        path.write_text(json.dumps({"id": plugin, "label": "Scene Maker", "team_folders": list(folders), **extra}),
                        encoding="utf-8")
        from backend.uefn_plugins import scopes

        tf._DECLS.clear()  # read afresh (two writes can share an mtime tick)
        scopes._SHARING.clear()

    @property
    def root(self) -> Path:
        return self.local / "UEFN-Ducky" / PLUGIN / "scenes"

    def binding(self, plugin: str = PLUGIN, project_root: str | None = "") -> tf.Binding:
        return tf.resolve(plugin, project_root=project_root)[0]

    def folder(self, plugin: str = PLUGIN) -> tf.Folder:
        return tf.plugin_folders(plugin)[0]

    def team(self, space: str = "spaceT", project_id: str = "") -> Path:
        return tf.team_copy(self.account, space, PLUGIN, self.folder(), project_id)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from backend.uefn_plugins import data_crypto
    from frontend import duckyos_account

    e = Env(monkeypatch, tmp_path)
    monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: e.key)
    data_crypto.reset_for_tests()
    monkeypatch.setattr(data_crypto, "_fetch_adk",
                        lambda: hashlib.sha256(b"adk/" + duckyos_account.account_key().encode()).digest())
    # Every test machine reads as one local NTFS drive with no OneDrive; refusals are tested on their own.
    monkeypatch.setattr(tf, "_volume", lambda path: ("NTFS", False))
    monkeypatch.setattr(tf, "_onedrive_roots", lambda: [])
    tf._DECLS.clear()
    e.manifest(SCENES)
    yield e
    data_crypto.reset_for_tests()
    _drop_junctions(e.local)
    _drop_junctions(tmp_path)


def _write(path: Path, data: bytes | str, *, mtime_ns: int | None = None) -> Path:
    tf._mkdirs(path.parent)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    with open(tf._lp(path), "wb") as f:
        f.write(raw)
    if mtime_ns is not None:
        os.utime(tf._lp(path), ns=(mtime_ns, mtime_ns))
    return path


def _read(path: Path) -> str:
    with open(tf._lp(path), "rb") as f:
        return f.read().decode("utf-8")


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _scenes(root: Path) -> dict[str, int]:
    """A Forge-like folder: two scenes and a loose file; returns {relpath: mtime_ns}."""
    files = {
        "MEGA/scene.json": '{"name": "MEGA"}', "MEGA/transforms.json": '{"base_stamp": 1}',
        "MEGA/history.json": "[]", "Duel/scene.json": '{"name": "Duel"}', "notes.txt": "hi",
    }
    out = {}
    for i, (rel, text) in enumerate(files.items()):
        out[rel] = MTIME + i * 100
        _write(root / rel, text, mtime_ns=out[rel])
    return out


def _incoming(env: Env, relpath: str, text: str, *, mtime_ns: int = MTIME, project_id: str = "",
              space: str = "spaceT") -> tf.Incoming:
    temp = tf.temp_for(env.account, space, PLUGIN, env.folder(), relpath, project_id)
    _write(temp, text)
    return tf.Incoming(relpath=relpath, op="put", temp=str(temp), mtime_ns=mtime_ns, sha256=_sha(text),
                       size=len(text.encode("utf-8")))


def _synced(env: Env, files: dict[str, str], *, seq: int = 1, space: str = "spaceT") -> None:
    """The team copy holds ``files`` at ``seq`` (as a pull put them there)."""
    folder = env.folder()
    by_unit: dict[str, list[tf.Incoming]] = {}
    for rel, text in files.items():
        by_unit.setdefault(tf.unit_of(folder, rel), []).append(_incoming(env, rel, text, space=space))
    for unit, items in by_unit.items():
        assert tf.apply_unit(env.account, space, PLUGIN, folder, unit, items, seq=seq)["ok"]



def test_a_plugin_that_declares_folders_is_offered_a_team(env: Env) -> None:
    from backend.uefn_plugins import scopes

    assert scopes.shareable(PLUGIN)
    st = tf.status(PLUGIN, project_root="")
    assert st["note"] == tf.PLAINTEXT_NOTE and st["folders"][0]["id"] == "scenes"
    assert (st["folders"][0]["refused"], st["folders"][0]["adopted"]) == ("", False)
    env.manifest(SCENES, data_sharing=False)
    assert not scopes.shareable(PLUGIN) and tf.plugin_folders(PLUGIN) == []
    env.manifest()  # no folders, no data: Local only
    assert not scopes.shareable(PLUGIN)


# --------------------------------------------------------------------------- adoption


def test_adoption_by_rename_keeps_the_plugins_files_behind_a_junction(env: Env) -> None:
    mtimes = _scenes(env.root)
    b = env.binding()
    assert tf.adopt(PLUGIN, b)["adopted"] == "rename"
    local = tf.local_copy(PLUGIN, env.folder())
    assert local == tf.app_root() / "scopes" / "_pc" / PLUGIN / "folders" / "scenes"
    assert tf._is_junction(env.root) and tf._key(tf.junction_target(env.root)) == tf._key(local)
    for rel, mtime in mtimes.items():  # the plugin sees the same files, to the 100 ns
        assert os.stat(env.root / rel).st_mtime_ns == mtime == os.stat(local / rel).st_mtime_ns
    link = repo.link_get(PLUGIN, "scenes")
    assert link["adopted_at"] > 0 and (link["account_id"], link["space_id"]) == ("_pc", "personal")
    assert tf.adopt(PLUGIN, b)["adopted"] == "already"  # once only


def test_adoption_by_copy_verifies_then_deletes_the_original(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    mtimes = _scenes(env.root)
    monkeypatch.setattr(tf, "_same_volume", lambda a, b: False)  # the folder is on another drive
    assert tf.adopt(PLUGIN, env.binding())["adopted"] == "copy"
    local = tf.local_copy(PLUGIN, env.folder())
    assert tf._is_junction(env.root)
    for rel, mtime in mtimes.items():
        assert os.stat(local / rel).st_mtime_ns == mtime
    assert not [p for p in env.root.parent.iterdir() if "ducky-moving" in p.name]

    # A copy that doesn't verify moves nothing: the folder stays the plugin's own.
    other = env.local / "UEFN-Ducky" / "other" / "scenes"
    _write(other / "A" / "scene.json", "{}")
    env.manifest({**SCENES, "root": "{LOCALAPPDATA}/UEFN-Ducky/other/scenes"}, plugin="other")
    real_sha = tf._sha_file
    monkeypatch.setattr(tf, "_sha_file", lambda p: "bad" if "_pc" in str(p) else real_sha(p))
    with pytest.raises(tf.FolderError, match="didn't give the same file"):
        tf.adopt("other", env.binding("other"))
    assert not tf._is_junction(other) and _read(other / "A" / "scene.json") == "{}"
    assert not tf.local_copy("other", env.folder("other")).exists()


def test_a_folder_in_use_is_not_adopted(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenes(env.root)
    real_rename = os.rename

    def busy(src: str, dst: str) -> None:
        if src.endswith("scenes"):
            raise PermissionError(5, "Access is denied")
        real_rename(src, dst)

    monkeypatch.setattr(tf.os, "rename", busy)
    with pytest.raises(tf.FolderError, match="in use"):
        tf.adopt(PLUGIN, env.binding())
    assert not tf._is_junction(env.root) and (env.root / "notes.txt").is_file()


# --------------------------------------------------------------------------- junctions


def test_junction_repoints_across_local_and_team_and_is_repaired(env: Env) -> None:
    _scenes(env.root)
    team = env.team()
    _write(team / "Arena" / "scene.json", '{"name": "Arena"}')
    before = repo.reload_epoch(PLUGIN)
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))
    assert out["ok"], out
    assert tf._key(tf.junction_target(env.root)) == tf._key(team)
    assert (env.root / "Arena" / "scene.json").is_file() and not (env.root / "MEGA").exists()
    assert repo.reload_epoch(PLUGIN) > before  # a process that loaded the plugin reloads it
    assert repo.link_get(PLUGIN, "scenes")["space_id"] == "spaceT"

    assert tf.switch(PLUGIN, "personal", project_root="")["ok"]
    local = tf.local_copy(PLUGIN, env.folder())
    assert tf._key(tf.junction_target(env.root)) == tf._key(local) and (env.root / "MEGA" / "scene.json").is_file()

    # Repair before load (the plugin is on Local): a missing junction is made again...
    os.rmdir(env.root)
    assert tf.bind_plugin(PLUGIN, project_root="")["ok"]
    assert tf._key(tf.junction_target(env.root)) == tf._key(local)
    # ...one pointing elsewhere is re-pointed (and what it pointed at is left alone)...
    elsewhere = env.tmp / "elsewhere"
    _write(elsewhere / "keep.txt", "keep")
    os.rmdir(env.root)
    import _winapi

    _winapi.CreateJunction(str(elsewhere), str(env.root))
    tf.bind_plugin(PLUGIN, project_root="")
    assert tf._key(tf.junction_target(env.root)) == tf._key(local) and _read(elsewhere / "keep.txt") == "keep"
    # ...and a real folder the plugin put in its place becomes changes in the copy in use.
    os.rmdir(env.root)
    _write(env.root / "MEGA" / "scene.json", '{"name": "MEGA", "edited": true}')
    _write(env.root / "New" / "scene.json", "{}")
    tf.bind_plugin(PLUGIN, project_root="")
    assert tf._is_junction(env.root)
    assert json.loads(_read(local / "MEGA" / "scene.json"))["edited"] and (local / "New" / "scene.json").is_file()
    assert (local / "Duel" / "scene.json").is_file()  # nothing the folder lacked was deleted


# --------------------------------------------------------------------------- refusals


def test_each_refusal_reason(env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = env.folder()
    root = env.root
    monkeypatch.setattr(tf, "_volume", lambda path: ("", True))
    assert tf.refusal(folder, root) == tf.REFUSE_NETWORK
    monkeypatch.setattr(tf, "_volume", lambda path: ("FAT32", False))
    assert tf.refusal(folder, root) == "This folder is on a FAT32 drive. Team folders need an NTFS drive."
    monkeypatch.setattr(tf, "_volume", lambda path: ("NTFS", False))
    monkeypatch.setattr(tf, "_onedrive_roots", lambda: [str(env.local)])
    assert tf.refusal(folder, root) == tf.REFUSE_ONEDRIVE
    monkeypatch.setattr(tf, "_onedrive_roots", lambda: [str(tmp_path / "OneDrive")])
    assert tf.refusal(folder, root) == ""

    # A {PROJECT} root inside UEFN revision control is carried by it, unless its ignore file leaves it out.
    env.manifest(DESIGN, plugin="rogue")
    design = env.folder("rogue")
    island = tmp_path / "Island"
    (island / ".urc").mkdir(parents=True)
    root = tf.resolve_root(design, str(island))
    assert tf.refusal(design, root, str(island)) == tf.REFUSE_URC
    (island / ".urcignore").write_text("# local tools\n.ducky/\n", encoding="utf-8")
    assert tf.refusal(design, root, str(island)) == ""
    (island / ".urcignore").write_text(".ducky/*\n!.ducky/roguelike-design/\n", encoding="utf-8")
    assert tf.refusal(design, root, str(island)) == tf.REFUSE_URC  # put back under revision control
    lore = tmp_path / "LoreIsland"
    (lore / ".lore").mkdir(parents=True)
    (lore / ".loreignore").write_text("/.ducky\n", encoding="utf-8")
    assert tf.refusal(design, tf.resolve_root(design, str(lore)), str(lore)) == ""
    assert tf.refusal(design, tf.resolve_root(design, str(tmp_path)), str(tmp_path)) == ""  # no workspace

    # A refused folder refuses the switch with the reason, and is left as it is.
    _scenes(env.root)
    monkeypatch.setattr(tf, "_onedrive_roots", lambda: [str(env.local)])
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))
    assert out == {"ok": False, "error": tf.REFUSE_ONEDRIVE, "refused": {"scenes": tf.REFUSE_ONEDRIVE}}
    assert not tf._is_junction(env.root)


def test_the_volume_probe_reads_this_drive(tmp_path: Path) -> None:
    fs, remote = tf._volume(tmp_path / "not" / "made" / "yet")
    assert remote is False and fs  # the nearest existing folder's drive is read (NTFS on a normal PC)


# --------------------------------------------------------------------------- capture


def test_the_quiet_rule_waits_3_s_and_for_tmp_files(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    team = env.team()
    for rel, text in {"MEGA/scene.json": "{}", "MEGA/history.json": "[]", "MEGA/history-snapshots/1.bin": "x",
                      "Duel/scene.json": "{}"}.items():
        _write(team / rel, text)
    now = time.time() + 0.01
    folder = env.folder()
    hashed: list[str] = []
    real_sha = tf._sha_file
    monkeypatch.setattr(tf, "_sha_file", lambda p: hashed.append(str(p)) or real_sha(p))
    first = tf.scan(env.account, "spaceT", PLUGIN, folder, now=now)
    assert first.changed == {"MEGA": 1, "Duel": 1} and first.quiet == [] and first.waiting == ["Duel", "MEGA"]
    assert {r["relpath"] for r in repo.files(env.account, "spaceT", PLUGIN, "scenes")} == {
        "MEGA/scene.json", "Duel/scene.json"}  # ignored files never become rows
    assert tf.scan(env.account, "spaceT", PLUGIN, folder, now=now + 3.5).quiet == ["Duel", "MEGA"]
    assert len(hashed) == 2  # unchanged files aren't read again

    # A save in progress (a *.tmp in the unit) holds it back however long ago it changed.
    _write(team / "MEGA" / "scene.json.4120.tmp", "{")
    later = tf.scan(env.account, "spaceT", PLUGIN, folder, now=now + 10)
    assert later.quiet == ["Duel"] and later.waiting == ["MEGA"] and later.tmp == {"MEGA"}
    os.unlink(team / "MEGA" / "scene.json.4120.tmp")
    _write(team / "MEGA" / "scene.json", '{"v": 2}')  # the save landed: quiet 3 s after it
    landed = os.stat(team / "MEGA" / "scene.json").st_mtime_ns / 1e9
    assert "MEGA" in tf.scan(env.account, "spaceT", PLUGIN, folder, now=landed + 1).waiting
    assert tf.scan(env.account, "spaceT", PLUGIN, folder, now=landed + 3.1).quiet == ["Duel", "MEGA"]
    assert not any(r["relpath"].endswith(".tmp") for r in repo.files(env.account, "spaceT", PLUGIN, "scenes"))


def test_a_change_back_to_the_teams_version_is_not_a_change_and_deletes_are(env: Env) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/transforms.json": "{}"})
    team, folder = env.team(), env.folder()
    assert tf.scan(env.account, "spaceT", PLUGIN, folder).changed == {}
    _write(team / "MEGA" / "scene.json", '{"v": 1}')  # same content, new mtime
    assert tf.scan(env.account, "spaceT", PLUGIN, folder).changed == {}
    os.unlink(team / "MEGA" / "transforms.json")
    _write(team / "MEGA" / "new.json", "{}")
    out = tf.scan(env.account, "spaceT", PLUGIN, folder)
    assert out.changed == {"MEGA": 2}
    rows = {r["relpath"]: r for r in repo.files(env.account, "spaceT", PLUGIN, "scenes")}
    assert (rows["MEGA/transforms.json"]["deleted"], rows["MEGA/transforms.json"]["dirty"]) == (1, 1)
    assert rows["MEGA/new.json"]["dirty"] == 1 and rows["MEGA/scene.json"]["dirty"] == 0



def test_a_sent_unit_is_clean_unless_it_changed_while_it_went_out(env: Env) -> None:
    env.manifest({**SCENES, "merge": ["*.json"]})
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/old.bin": "old"})
    team, folder = env.team(), env.folder()
    _write(team / "MEGA" / "scene.json", '{"v": 2}')
    _write(team / "MEGA" / "mesh.bin", "mesh")
    os.unlink(team / "MEGA" / "old.bin")
    tf.scan(env.account, "spaceT", PLUGIN, folder)
    out = {i["relpath"]: i for i in tf.outgoing(env.account, "spaceT", PLUGIN, folder, "MEGA")}
    assert {k: (v["op"], v["base_seq"]) for k, v in out.items()} == {
        "MEGA/scene.json": ("put", 1), "MEGA/mesh.bin": ("put", 0), "MEGA/old.bin": ("del", 1)}
    assert _read(Path(tf._plain(out["MEGA/mesh.bin"]["path"]))) == "mesh"
    _write(team / "MEGA" / "mesh.bin", "mesh, edited while it went out")
    tf.scan(env.account, "spaceT", PLUGIN, folder)
    assert tf.mark_sent(env.account, "spaceT", PLUGIN, folder, out.values(), 5) == {"clean": 2, "kept": 1}
    rows = {r["relpath"]: r for r in repo.files(env.account, "spaceT", PLUGIN, "scenes")}
    assert (rows["MEGA/scene.json"]["dirty"], rows["MEGA/scene.json"]["base_seq"]) == (0, 5)
    assert (rows["MEGA/mesh.bin"]["dirty"], rows["MEGA/mesh.bin"]["base_seq"]) == (1, 5)
    assert "MEGA/old.bin" not in rows
    assert repo.base_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["seq"] == 5
    assert repo.copy_get(env.account, "spaceT", PLUGIN, "scenes")["pushed_at"] > 0


# --------------------------------------------------------------------------- apply


def test_apply_order_and_exact_mtime_ns(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/old.bin": "old"})
    team, folder = env.team(), env.folder()
    items = [_incoming(env, "MEGA/scene.json", '{"v": 2}', mtime_ns=MTIME + 900),
             _incoming(env, "MEGA/transforms.json", '{"base_stamp": 1789000000123457600}', mtime_ns=MTIME + 500),
             _incoming(env, "MEGA/mesh.bin", "mesh", mtime_ns=MTIME + 700),
             tf.Incoming(relpath="MEGA/old.bin", op="del")]
    seen: list[str] = []
    real_replace, real_utime = os.replace, os.utime

    def replace(src: str, dst: str) -> None:
        if str(dst).endswith(".tmp"):
            seen.append("aside " + Path(str(src)).name)
        real_replace(src, dst)

    def utime(path: str, *a: Any, **k: Any) -> None:
        seen.append("put " + Path(str(path)).name)
        real_utime(path, *a, **k)

    monkeypatch.setattr(tf.os, "replace", replace)
    monkeypatch.setattr(tf.os, "utime", utime)
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", items, seq=2)
    assert out["ok"] and out["state"] == "applied"
    puts = [s for s in seen if s.startswith("put ")]
    assert puts == ["put mesh.bin", "put transforms.json", "put scene.json"]  # others, then last
    assert seen.index("aside old.bin") > seen.index("put scene.json")  # deletes after
    for rel, mtime in (("MEGA/scene.json", MTIME + 900), ("MEGA/transforms.json", MTIME + 500),
                       ("MEGA/mesh.bin", MTIME + 700)):
        assert os.stat(team / rel).st_mtime_ns == mtime
        row = repo.file_get(env.account, "spaceT", PLUGIN, "scenes", rel)
        assert (row["mtime_ns"], row["base_seq"], row["dirty"]) == (mtime, 2, 0)
    assert not (team / "MEGA" / "old.bin").exists()
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/old.bin") is None
    assert tf.scan(env.account, "spaceT", PLUGIN, folder).changed == {}  # nothing to send back
    assert not [p for p in (team / "MEGA").iterdir() if p.name.endswith(".tmp")]


def test_a_failed_file_puts_the_whole_unit_back(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/transforms.json": '{"t": 1}'})
    team, folder = env.team(), env.folder()
    items = [_incoming(env, "MEGA/transforms.json", '{"t": 2}'), _incoming(env, "MEGA/scene.json", '{"v": 2}')]
    real_replace = os.replace

    def replace(src: str, dst: str) -> None:
        if str(dst).endswith("scene.json") and Path(str(src)).name.startswith(".scene.json."):  # the download
            raise PermissionError(32, "in use")
        real_replace(src, dst)

    monkeypatch.setattr(tf.os, "replace", replace)
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", items, seq=2)
    assert out["state"] == "blocked" and "in use" in out["error"]
    assert _read(team / "MEGA" / "transforms.json") == '{"t": 1}' and _read(team / "MEGA" / "scene.json") == '{"v": 1}'
    assert not [p for p in (team / "MEGA").iterdir() if p.name.endswith(".tmp")]
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/transforms.json")["base_seq"] == 1


def test_os_replace_onto_a_read_only_target_fails(env: Env) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}'})
    team, folder = env.team(), env.folder()
    target = team / "MEGA" / "scene.json"
    uid = lambda unit: tf.tree_unit(None, "spaceT", "scenes", unit)  # noqa: E731
    from backend.store.repos import space_vcs

    space_vcs.lock_put(env.account, "spaceT", PLUGIN, uid("MEGA"), name="MEGA", holder_name="Ana", lease_left=300,
                       since=time.time())
    assert tf.refresh_readonly(env.account, "spaceT", PLUGIN, folder, unit_id=uid)["readonly"] == ["MEGA"]
    assert tf._readonly(target)
    _write(team / "MEGA" / "scene.json.save.tmp", '{"v": "mine"}')  # how a plugin saves: temp, then replace
    with pytest.raises(PermissionError):
        os.replace(team / "MEGA" / "scene.json.save.tmp", target)
    assert _read(target) == '{"v": 1}'
    # Ana's own change still lands (core clears the attribute for its apply), read-only again after.
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA",
                        [_incoming(env, "MEGA/scene.json", '{"v": "ana"}')], seq=2, held=True)
    assert out["ok"] and _read(target) == '{"v": "ana"}' and tf._readonly(target)
    space_vcs.lock_delete(env.account, "spaceT", PLUGIN, [uid("MEGA")])
    assert tf.refresh_readonly(env.account, "spaceT", PLUGIN, folder, unit_id=uid)["writable"] == ["MEGA"]
    os.replace(team / "MEGA" / "scene.json.save.tmp", target)
    assert _read(target) == '{"v": "mine"}'


def test_a_dirty_unit_is_never_overwritten(env: Env) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/transforms.json": '{"t": 1}'})
    team, folder = env.team(), env.folder()
    _write(team / "MEGA" / "scene.json", '{"v": "mine"}')
    tf.scan(env.account, "spaceT", PLUGIN, folder)
    theirs = [_incoming(env, "MEGA/transforms.json", '{"t": 2}')]
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", theirs, seq=2)
    assert out["state"] == "conflict" and out["mine"] == ["MEGA/scene.json"]
    assert _read(team / "MEGA" / "scene.json") == '{"v": "mine"}'
    assert _read(team / "MEGA" / "transforms.json") == '{"t": 1}'
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["sync_state"] == "conflict"
    assert "MEGA" in tf.scan(env.account, "spaceT", PLUGIN, folder).held  # held back from pushes

    # An edit the scan hasn't seen yet counts too.
    _synced(env, {"Duel/scene.json": '{"v": 1}'})
    _write(team / "Duel" / "scene.json", '{"v": "unscanned"}')
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "Duel",
                        [_incoming(env, "Duel/scene.json", '{"v": 2}')], seq=3)
    assert out["state"] == "conflict" and _read(team / "Duel" / "scene.json") == '{"v": "unscanned"}'

    # Take theirs is the person's choice.
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA",
                        [_incoming(env, "MEGA/transforms.json", '{"t": 2}')], seq=2, force=True)
    assert out["ok"] and _read(team / "MEGA" / "transforms.json") == '{"t": 2}'
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["sync_state"] == "refetch"


def test_json_files_a_declaration_merges_merge_three_ways(env: Env) -> None:
    env.manifest({**SCENES, "merge": ["*.json"]})
    _synced(env, {"MEGA/scene.json": json.dumps({"a": 1, "b": 1}, indent=2) + "\n", "MEGA/mesh.bin": "m"})
    team, folder = env.team(), env.folder()
    assert repo.base_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["seq"] == 1
    assert repo.base_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/mesh.bin") is None  # not JSON
    _write(team / "MEGA" / "scene.json", json.dumps({"a": 2, "b": 1}, indent=2) + "\n")
    tf.scan(env.account, "spaceT", PLUGIN, folder)
    theirs = json.dumps({"a": 1, "b": 3}, indent=2) + "\n"
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", [_incoming(env, "MEGA/scene.json", theirs)],
                        seq=2)
    assert out["state"] == "merged" and json.loads(_read(team / "MEGA" / "scene.json")) == {"a": 2, "b": 3}
    row = repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")
    assert (row["dirty"], row["base_seq"], row["base_sha"]) == (1, 2, _sha(theirs))  # the merge goes out
    # A real clash keeps the whole unit back.
    clash = _incoming(env, "MEGA/scene.json", json.dumps({"a": 9, "b": 3}))
    assert tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", [clash], seq=3)["state"] == "conflict"
    assert json.loads(_read(team / "MEGA" / "scene.json")) == {"a": 2, "b": 3}
    assert tf.mergeable(folder, "MEGA/big.json", tf.MERGE_MAX_BYTES + 1) is False


# --------------------------------------------------------------------------- {PROJECT} roots


def _island(tmp_path: Path, name: str, guid: str = GUID) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    manifest = {"title": name, "plugins": [{"name": guid, "bIsRoot": True}]}
    (root / f"{name}.uefnproject").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_project_roots_keep_one_copy_per_project_id(env: Env, tmp_path: Path) -> None:
    env.manifest(DESIGN, plugin="rogue")
    design = env.folder("rogue")
    one, two = _island(tmp_path, "Island"), _island(tmp_path, "Other", "aaaa-bbbb")
    _write(one / ".ducky" / "roguelike-design" / "store.json", '{"rooms": 1}')
    _write(two / ".ducky" / "roguelike-design" / "store.json", '{"rooms": 2}')
    b1, b2 = env.binding("rogue", str(one)), env.binding("rogue", str(two))
    assert (b1.project_id, b2.project_id) == (GUID.lower(), "aaaa-bbbb")
    tf.adopt("rogue", b1)
    tf.adopt("rogue", b2)
    pc = tf.app_root() / "scopes" / "_pc" / "rogue" / "folders" / "design"
    assert _read(pc / GUID.lower() / "store.json") == '{"rooms": 1}' and _read(pc / "aaaa-bbbb" / "store.json") == \
        '{"rooms": 2}'
    assert tf._key(tf.junction_target(one / ".ducky" / "roguelike-design")) == tf._key(pc / GUID.lower())
    team = tf.team_copy(env.account, "spaceT", "rogue", design, GUID.lower())
    assert tf._key(team) == tf._key(tf.app_root() / "scopes" / env.account / "spaceT" / "rogue" / "folders" / "design"
                                    / GUID.lower())  # long-path form on Windows: the same folder
    _write(team / "store.json", "{}")
    _write(team / "store.prev.json", "{}")  # ignored
    out = tf.scan(env.account, "spaceT", "rogue", design, project_id=GUID.lower(), now=time.time() + 5)
    assert out.quiet == [GUID.lower()]  # '.': the whole folder of that project is one unit
    assert [(r["relpath"], r["unit"], r["project_id"]) for r in repo.files(env.account, "spaceT", "rogue", "design")] \
        == [(f"{GUID.lower()}/store.json", GUID.lower(), GUID.lower())]
    assert tf.resolve("rogue", project_root="")[0].root is None  # no island open: nothing to bind


# --------------------------------------------------------------------------- calls in every process


_HOLDER = """
import os, sys, time
sys.path.insert(0, {app!r})
from backend.store.repos import folder_files
print(folder_files.call_add({plugin!r}, os.getpid()), flush=True)
time.sleep(60)
"""


def test_a_repoint_waits_for_calls_in_other_processes_and_ignores_dead_ones(env: Env) -> None:
    _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    app = str(Path(__file__).resolve().parents[2])
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER.format(app=app, plugin=PLUGIN)], stdout=subprocess.PIPE,
                            text=True, env={**os.environ})
    try:
        key = proc.stdout.readline().strip()
        assert key.startswith("plugin_call:scene-maker:")
        assert [c["key"] for c in tf.live_calls(PLUGIN)] == [key]
        _write(env.team() / "Arena" / "scene.json", "{}")
        out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""), wait_s=0.3)
        assert out == {"ok": False, "error": "Scene Maker is still busy with a call. Try again when it finishes."}
        assert tf._key(tf.junction_target(env.root)) == tf._key(tf.local_copy(PLUGIN, env.folder()))
    finally:
        proc.kill()
        proc.wait(10)
    # The process is gone: its marker is ignored and dropped, and the switch goes ahead.
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""), wait_s=0.3)
    assert out["ok"] and repo.calls(PLUGIN) == {}
    assert tf._key(tf.junction_target(env.root)) == tf._key(env.team())


def test_a_repoint_waits_for_a_running_call_but_never_for_its_own(env: Env) -> None:
    _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    _write(env.team() / "Arena" / "scene.json", "{}")
    started, ended = threading.Event(), []

    def call() -> None:
        with tf.call_marker(PLUGIN, actor="agent", ducky="Builder"):
            started.set()
            time.sleep(0.6)
            ended.append(time.monotonic())

    t = threading.Thread(target=call)
    t.start()
    started.wait(5)
    assert tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))["ok"]
    moved = time.monotonic()
    t.join()
    assert ended and moved >= ended[0]  # the re-point came after the call returned
    assert repo.last_call(PLUGIN)["ducky"] == "Builder"
    with tf.call_marker(PLUGIN):  # a plugin call that switches its own scope doesn't wait for itself
        assert tf.wait_calls(PLUGIN, 0.0)
        assert tf.switch(PLUGIN, "personal", project_root="", wait_s=0.0)["ok"]


def test_tool_calls_of_a_folder_plugin_leave_a_marker_while_they_run(env: Env) -> None:
    from backend.uefn_plugins import scopes

    seen: list[int] = []
    with scopes.write_group(PLUGIN, actor="agent", ducky="Builder", message="scene_save"):
        seen.append(len(repo.calls(PLUGIN)))
        with scopes.write_group(PLUGIN):  # a panel RPC calling a tool: one call
            seen.append(len(repo.calls(PLUGIN)))
    with scopes.write_group(PLUGIN, panel=True):  # a panel's own data call
        seen.append(len(repo.calls(PLUGIN)))
    with scopes.write_group("no-folders"):
        seen.append(len(repo.calls("no-folders")))
    assert seen == [1, 1, 0, 0] and repo.calls(PLUGIN) == {}


# --------------------------------------------------------------------------- plugin tests


def test_a_plugin_test_points_the_folders_at_a_throwaway_copy(env: Env) -> None:
    from backend.uefn_plugins import scopes

    _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    local = tf.local_copy(PLUGIN, env.folder())
    team = env.team()
    _write(team / "MEGA" / "scene.json", "{}")
    scope = scopes.sandbox_begin(PLUGIN)
    try:
        assert not any(env.root.iterdir())  # the test starts empty
        _write(env.root / "Test" / "scene.json", "{}")
        assert tf.sandboxed(PLUGIN)
        assert tf.scan(env.account, "spaceT", PLUGIN, env.folder()).files == 0  # never captured meanwhile
        assert scope["id"] in tf.junction_target(env.root)
    finally:
        scopes.sandbox_end(PLUGIN)
    assert tf._key(tf.junction_target(env.root)) == tf._key(local)
    assert (env.root / "MEGA" / "scene.json").is_file() and not (local / "Test").exists()
    assert not tf.sandboxed(PLUGIN) and not (scopes.scopes_root(env.account) / scope["id"]).exists()
    # A flag left by a test process that is gone is dropped.
    repo.sandbox_put(PLUGIN, {"pid": 999_999_991, "scope": "x"})
    assert tf.sandboxed(PLUGIN) is False and repo.sandbox_get(PLUGIN) is None


# --------------------------------------------------------------------------- names


def test_names_hash_after_nfc_and_casefold_and_clashes_are_refused(env: Env) -> None:
    nfc, nfd = "Scène.json", "Scène.json"
    assert tf.tree_key(None, "s", "scenes", "MEGA", f"MEGA/{nfc}") == tf.tree_key(None, "s", "scenes", "mega",
                                                                                 f"mega/{nfd.upper()}")
    asked: list[tuple[str, ...]] = []

    class Backend:
        def tree_key(self, space: str, folder: str, unit: str, relpath: str) -> str:
            asked.append((unit, relpath))
            return "tree:scenes/k"

    tf.tree_key(Backend(), "s", "scenes", "MEGA", f"MEGA/{nfd}")
    assert asked == [("mega", f"mega/{nfc.casefold()}")]

    team, folder = env.team(), env.folder()
    _write(team / "MEGA" / nfc, "{}")
    _write(team / "MEGA" / nfd, "{}")  # NTFS keeps both: they differ only by Unicode form
    _write(team / "Duel" / "scene.json", "{}")
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert set(out.refused) == {"MEGA"} and "differ only by upper/lower case or accents" in out.refused["MEGA"]
    assert out.held == ["MEGA"] and out.quiet == ["Duel"]
    os.unlink(tf._lp(team / "MEGA" / nfd))
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert out.refused == {} and out.quiet == ["Duel", "MEGA"]


def test_a_file_over_the_teams_limit_refuses_its_unit_before_anything_goes(env: Env,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tf, "FILE_MAX_BYTES", 16)  # stands in for 4 GiB
    team, folder = env.team(), env.folder()
    _write(team / "MEGA" / "scene.json", "{}")
    _write(team / "MEGA" / "baked.bin", b"x" * 17)
    _write(team / "Duel" / "scene.json", "{}")
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert out.refused == {"MEGA": tf.TOO_BIG + "MEGA/baked.bin"} and out.held == ["MEGA"] and out.quiet == ["Duel"]
    _write(team / "MEGA" / "baked.bin", b"x" * 16)  # made small enough: the unit goes again
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert out.refused == {} and out.quiet == ["Duel", "MEGA"]


def test_folder_files_the_server_refused_go_again_when_the_plan_changes(env: Env) -> None:
    from backend.store.repos import plugin_data

    team, folder = env.team(), env.folder()
    _write(team / "MEGA" / "scene.json", "{}")
    tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert repo.unit_set_state(env.account, "spaceT", PLUGIN, "scenes", "MEGA", "refused",
                               "Team storage is full (5 of 5 GB)") == 1
    assert tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5).held == ["MEGA"]
    assert plugin_data.requeue_refused(env.account, "spaceT") == 1
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert out.held == [] and out.quiet == ["MEGA"]


# --------------------------------------------------------------------------- long paths


def test_paths_over_260_characters_work(env: Env) -> None:
    team, folder = env.team(), env.folder()
    rel = "MEGA/" + "/".join(["d" * 50] * 5) + "/mesh.bin"
    assert len(str(team / rel)) > 260
    _write(team / rel, "mesh", mtime_ns=MTIME)
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 5)
    assert out.quiet == ["MEGA"] and repo.file_get(env.account, "spaceT", PLUGIN, "scenes", rel)["sha256"] == \
        _sha("mesh")
    item = _incoming(env, rel, "mesh 2", mtime_ns=MTIME + 100)
    assert tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", [item], seq=2, force=True)["ok"]
    assert os.stat(tf._lp(team / rel)).st_mtime_ns == MTIME + 100 and _read(team / rel) == "mesh 2"
    tf.protect(env.account, "spaceT", PLUGIN, folder, ["MEGA"], True)
    assert tf.remove_tree(team) and not team.exists()  # read-only files and deep paths go too


# --------------------------------------------------------------------------- first share + migration


def test_a_switch_pulls_first_and_a_failed_pull_changes_nothing(env: Env) -> None:
    _scenes(env.root)
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (False, "Alpha is offline."))
    assert out == {"ok": False, "error": "Alpha is offline."}
    local = tf.local_copy(PLUGIN, env.folder())
    assert tf._key(tf.junction_target(env.root)) == tf._key(local)  # adopted, still on the copy in use
    assert (env.root / "MEGA" / "scene.json").is_file()

    def pull() -> tuple[bool, str]:  # the team's copy arrives while the junction is still on Local
        assert tf._key(tf.junction_target(env.root)) == tf._key(local)
        _write(env.team() / "Arena" / "scene.json", "{}")
        return True, ""

    assert tf.switch(PLUGIN, "spaceT", project_root="", pull=pull)["ok"]
    assert sorted(p.name for p in env.root.iterdir()) == ["Arena"]
    assert repo.copy_get(env.account, "spaceT", PLUGIN, "scenes")["ready"] == 1


def test_an_empty_team_copy_is_bound_only_after_the_first_share(env: Env) -> None:
    mtimes = _scenes(env.root)
    pushed: list[int] = []
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))
    assert out["needs"] == "first_share" and out["note"] == tf.PLAINTEXT_NOTE and out["ok"] is False
    assert [(u["unit"], u["files"]) for u in out["units"]] == [("Duel", 1), ("MEGA", 2), ("notes.txt", 1)]
    assert tf._key(tf.junction_target(env.root)) == tf._key(tf.local_copy(PLUGIN, env.folder()))
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""), push=lambda: pushed.append(1) or (
        True, ""), first_share={"units": ["MEGA"]})
    assert out["ok"] and pushed == [1]
    assert sorted(p.name for p in env.root.iterdir()) == ["MEGA"]
    assert os.stat(env.root / "MEGA" / "scene.json").st_mtime_ns == mtimes["MEGA/scene.json"]
    assert not (env.root / "MEGA" / "history.json").exists()  # ignored files stay Local

    # Start empty binds an empty copy; nothing is sent.
    env.manifest({**SCENES, "root": "{LOCALAPPDATA}/UEFN-Ducky/other/scenes"}, plugin="other")
    _write(env.local / "UEFN-Ducky" / "other" / "scenes" / "A" / "scene.json", "{}")
    out = tf.switch("other", "spaceT", project_root="", pull=lambda: (True, ""), first_share={"start_empty": True})
    assert out["ok"] and not any((env.local / "UEFN-Ducky" / "other" / "scenes").iterdir())


def test_a_link_made_before_folders_were_kept_goes_back_to_local_with_an_offer(env: Env) -> None:
    from backend.store.repos import plugin_data
    from backend.uefn_plugins import scopes

    _scenes(env.root)
    plugin_data.link_set(env.account, PLUGIN, "spaceT")
    plugin_data.sync_put(env.account, "spaceT", label="Alpha Studio", synced_at=time.time())
    assert scopes.active_scope(PLUGIN)["kind"] == "shared"
    out = tf.bind_plugin(PLUGIN, project_root="")
    assert out["ok"] and scopes.active_scope(PLUGIN)["kind"] == "personal"
    assert tf.offers(PLUGIN) == [{"space": "spaceT", "label": "Alpha Studio"}]
    assert not tf._is_junction(env.root) and (env.root / "MEGA" / "scene.json").is_file()  # untouched
    # Taking the offer is the first-share switch; the offer goes.
    assert tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""),
                     first_share={"units": ["MEGA"]})["ok"]
    assert tf.offers(PLUGIN) == []
    plugin_data.link_set(env.account, PLUGIN, "spaceT")
    assert tf.bind_plugin(PLUGIN, project_root="")["folders"][0]["state"] == "team"
    assert scopes.active_scope(PLUGIN)["kind"] == "shared"


# --------------------------------------------------------------------------- held units


def test_changes_to_a_unit_someone_holds_are_quarantined_then_kept_as_a_copy_or_discarded(env: Env) -> None:
    from backend.store.repos import space_vcs

    env.manifest({**SCENES, "on_locked": "quarantine"})
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "Duel/scene.json": '{"v": 1}'})
    team, folder = env.team(), env.folder()
    uid = lambda unit: tf.tree_unit(None, "spaceT", "scenes", unit)  # noqa: E731
    for unit in ("MEGA", "Duel"):
        space_vcs.lock_put(env.account, "spaceT", PLUGIN, uid(unit), holder_name="Ana", lease_left=300)
    assert tf.refresh_readonly(env.account, "spaceT", PLUGIN, folder, unit_id=uid)["readonly"] == []  # writable
    _write(team / "MEGA" / "scene.json", '{"v": "mine"}')
    _write(team / "MEGA" / "extra.json", "{}")
    _write(team / "Duel" / "scene.json", '{"v": "mine"}')
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 10, unit_id=uid)
    assert out.held == ["Duel", "MEGA"] and out.quiet == []  # never sent
    q = {r["unit"]: r for r in tf.quarantined(env.account, "spaceT", PLUGIN)}
    assert q["MEGA"]["reason"] == "Changed here while Ana holds it" and q["MEGA"]["files"] == 2
    table = {u["unit"]: u for u in tf.units_table(env.account, "spaceT", PLUGIN, folder, unit_id=uid)}
    assert table["MEGA"]["state"] == "quarantined"

    kept = tf.keep_mine_as_copy(env.account, "spaceT", PLUGIN, folder, "MEGA", holder="Ana")
    assert Path(kept["copy"]).name == "MEGA (Ana's copy)"
    assert _read(team / "MEGA (Ana's copy)" / "scene.json") == '{"v": "mine"}'
    assert (team / "MEGA (Ana's copy)" / "extra.json").is_file()
    assert kept["refetch"] == ["MEGA/scene.json"] and kept["dropped"] == ["MEGA/extra.json"]
    assert [r["relpath"] for r in tf.refetch_items(env.account, "spaceT", PLUGIN)] == ["MEGA/scene.json"]
    gone = tf.discard_mine(env.account, "spaceT", PLUGIN, folder, "Duel")
    assert gone["refetch"] == ["Duel/scene.json"] and not (team / "Duel" / "scene.json").exists()
    assert tf.quarantined(env.account, "spaceT", PLUGIN) == []
    # The team's version, fetched again, lands as clean.
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "Duel", [_incoming(env, "Duel/scene.json", '{"v": 1}')],
                        seq=1)
    assert out["ok"] and repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "Duel/scene.json")["sync_state"] == ""


def test_copy_local_into_a_team_keeps_the_teams_units(env: Env) -> None:
    mtimes = _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    team, folder = env.team(), env.folder()
    _write(team / "Duel" / "scene.json", '{"theirs": true}')
    out = tf.copy_local_into(PLUGIN, folder, env.account, "spaceT", ["MEGA", "Duel"])
    assert (out["copied"], out["kept"]) == (2, 1)
    assert os.stat(team / "MEGA" / "transforms.json").st_mtime_ns == mtimes["MEGA/transforms.json"]
    assert _read(team / "Duel" / "scene.json") == '{"theirs": true}'
    assert not (team / "notes.txt").exists()


# --------------------------------------------------------------------------- erase


def test_purging_a_space_moves_its_junctions_back_to_local_first(env: Env, tmp_path: Path) -> None:
    from backend.uefn_plugins import scopes

    _scenes(env.root)
    _write(env.team() / "Arena" / "scene.json", "{}")
    assert tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))["ok"]
    tf.scan(env.account, "spaceT", PLUGIN, env.folder())
    tf.protect(env.account, "spaceT", PLUGIN, env.folder(), ["Arena"], True)
    outside = tmp_path / "outside"
    _write(outside / "keep.txt", "keep")
    import _winapi

    _winapi.CreateJunction(str(outside), str(env.team() / "link"))  # a link inside the copy: never followed
    scopes.purge_space(env.account, "spaceT")
    local = tf.local_copy(PLUGIN, env.folder())
    assert tf._key(tf.junction_target(env.root)) == tf._key(local) and (env.root / "MEGA").is_dir()
    assert not (scopes.scopes_root(env.account) / "spaceT").exists()
    assert _read(outside / "keep.txt") == "keep"
    assert repo.files(env.account, "spaceT", PLUGIN, "scenes") == []


# --------------------------------------------------------------------------- App Data


def test_app_data_counts_a_folder_once_and_never_deletes_through_its_junction(env: Env) -> None:
    from frontend import appdata_maintenance as am

    _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    root = tf.app_root()
    plugin_row = next(r for r in am.appdata_overview(root)["items"] if r["name"] == PLUGIN)
    assert plugin_row["files"] == 0  # its folder is a link: counted under scopes
    scopes_row = next(r for r in am.appdata_overview(root)["items"] if r["name"] == "scopes")
    assert scopes_row["files"] == 5 and scopes_row["protected"] and not scopes_row["deletable"]
    pc = next(r for r in am.appdata_children("scopes", root)["items"] if r["name"] == "_pc")
    assert pc["label"] == "Local folder copies"
    link = am.appdata_children(PLUGIN, root)["items"][0]
    assert link["rel"] == f"{PLUGIN}/scenes" and "Points at this folder's copy" in link["description"]
    assert am.appdata_clear("scopes", root)["error"] == "protected"
    assert am.appdata_delete(f"{PLUGIN}/scenes", root)["ok"]
    assert not env.root.exists() and (tf.local_copy(PLUGIN, env.folder()) / "MEGA" / "scene.json").is_file()


# --------------------------------------------------------------------------- the engine: two PCs, one team
#
# Two PCs (each its own LOCALAPPDATA: database, folders, plugin install, account) on one
# fake team server (FakeLedger), driven through the same calls the bar and the scheduler
# make: the switch (pull first, first share), rounds, Update now, the choices.

SPACE = "spaceT"
LOOSE = {**SCENES, "lock": "none"}  # no locks: both PCs can change one unit


class TeamService:
    """A team service whose ledger is a fake server: one client per account (each PC)."""

    def __init__(self) -> None:
        from backend.uefn_plugins import ledger_conformance as conf
        from backend.uefn_plugins import spaces

        clients: dict[str, Any] = {}

        class _Provider(spaces.Provider):
            def enabled(self) -> bool:
                return True

            def ledger(self, account: str, space_id: str, plugin: str) -> Any:
                return clients.get(account) if space_id == SPACE else None

        self.server = conf.FakeLedgerServer()
        self.clients = clients
        self.provider = _Provider()

    def head(self, plugin: str = PLUGIN) -> int:
        return self.server.space(SPACE).repo(plugin).head

    def content(self, relpath: str, plugin: str = PLUGIN) -> bytes | None:
        """The team's current version of a folder file (by its real name)."""
        for item in self.server.space(SPACE).repo(plugin).items.values():
            if item.kind == "tree" and item.meta.get("relpath") == relpath:
                return item.content if item.op == "put" else None
        return None


class Machine:
    """One PC. ``with pc:`` makes it the PC the code runs on."""

    def __init__(self, env: Env, team: TeamService, name: str, display: str) -> None:
        self.env, self.team, self.name, self.display = env, team, name, display
        self.home = env.tmp / f"pc-{name}"
        (self.home / "Roaming").mkdir(parents=True, exist_ok=True)
        self.key = f"https://uefnducky.org|{name}@x.org"

    def __enter__(self) -> "Machine":
        from backend.uefn_plugins import data_crypto, scopes, spaces, vcs_engine

        mp = self.env.monkeypatch
        mp.setenv("LOCALAPPDATA", str(self.home))
        mp.setenv("APPDATA", str(self.home / "Roaming"))
        mp.setattr(vcs_engine, "_my_name", lambda: self.display)
        self.env.key = self.key
        data_crypto.reset_for_tests()  # this PC's keys (it drops the provider too)
        spaces.install_for_tests(self.team.provider)
        tf._DECLS.clear()
        scopes._SHARING.clear()
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    @property
    def account(self) -> str:
        from backend.uefn_plugins import scopes

        return scopes.account_id()

    @property
    def backend(self) -> Any:
        return self.team.clients[self.account]

    def folder(self) -> tf.Folder:
        return tf.plugin_folders(PLUGIN)[0]

    @property
    def root(self) -> Path:
        return tf.resolve_root(self.folder(), "")

    def copy(self) -> Path:
        return tf.team_copy(self.account, SPACE, PLUGIN, self.folder())

    def join(self, *folders: dict[str, Any]) -> None:
        """The plugin installed here (its manifest) and the team known, with a client."""
        from backend.store.repos import plugin_data
        from backend.uefn_plugins import vcs_engine

        self.env.manifest(*folders)
        plugin_data.sync_put(self.account, SPACE, label="Alpha Studio")
        self.team.clients[self.account] = self.team.server.client(f"u_{self.name}", vcs_engine.device_id(),
                                                                  name=self.display)

    def switch(self, scope: str = SPACE, **share: Any) -> dict[str, Any]:
        """What the bar's switch does for the folders, then the link."""
        from backend.uefn_plugins import scopes, spaces

        out = spaces.switch_folders(PLUGIN, scope, first_share=share or None)
        if out and out.get("ok"):
            scopes.link_plugin(PLUGIN, scope)
        return out

    def round(self, **kw: Any) -> dict[str, Any]:
        from backend.uefn_plugins import vcs_engine

        return vcs_engine.run(self.account, SPACE, PLUGIN, self.backend, force=True, **kw)

    def rows(self) -> dict[str, dict[str, Any]]:
        from backend.uefn_plugins import vcs_engine

        return {r["unit"]: r for r in vcs_engine.folder_rows(self.account, SPACE, PLUGIN, self.backend)}


@pytest.fixture
def team(env: Env, monkeypatch: pytest.MonkeyPatch):
    from backend.uefn_plugins import spaces

    svc = TeamService()
    spaces.install_for_tests(svc.provider)
    # A choice starts a background round in the app; here each test runs its own rounds.
    monkeypatch.setattr(spaces, "sync_space", lambda account, space, **kw: {"ok": True, "started": False})
    yield svc
    spaces.reset_for_tests()
    svc.server.rmtree()


@pytest.fixture
def pcs(env: Env, team: TeamService) -> tuple[Machine, Machine]:
    return Machine(env, team, "ana", "Ana"), Machine(env, team, "ben", "Ben")


def _share_scenes(pc: Machine, units: list[str], decl: dict[str, Any] = SCENES) -> None:
    """``pc`` joins, its scenes folder has MEGA and Duel, and it shares ``units`` first."""
    pc.join(decl)
    _write(pc.root / "MEGA" / "scene.json", '{"name": "MEGA"}', mtime_ns=MTIME)
    _write(pc.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1]}),
           mtime_ns=MTIME + 500)
    _write(pc.root / "Duel" / "scene.json", '{"v": 1}', mtime_ns=MTIME + 600)
    asked = pc.switch()
    assert asked["needs"] == "first_share" and asked["note"] == tf.PLAINTEXT_NOTE
    assert pc.switch(units=units)["ok"]


def test_a_forge_scene_round_trips_between_two_pcs(env: Env, pcs: tuple[Machine, Machine], team: TeamService) -> None:
    from backend.uefn_plugins import vcs_engine

    ana, ben = pcs
    blob = os.urandom(30 * 1024 * 1024)
    with ana:
        ana.join(SCENES)
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA"}', mtime_ns=MTIME)
        # Forge's moves name the scene.json they were made on by its mtime.
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1]}),
               mtime_ns=MTIME + 500)
        _write(ana.root / "MEGA" / "mesh.bin", blob, mtime_ns=MTIME + 700)
        _write(ana.root / "MEGA" / "history.json", "[]")
        _write(ana.root / "Duel" / "scene.json", "{}")
        asked = ana.switch()
        assert asked.get("needs") == "first_share", asked
        assert [u["unit"] for u in asked["units"]] == ["Duel", "MEGA"]
        assert ana.switch(units=["MEGA"])["ok"]  # copied into the team's copy, sent, then used
        assert team.head() == 1 and team.content("MEGA/mesh.bin") == blob
        assert team.content("MEGA/history.json") is None  # ignored: never shared
        assert sorted(p.name for p in ana.root.iterdir()) == ["MEGA"]
        assert ana.rows()["MEGA"]["state"] == "up_to_date" and ana.rows()["MEGA"]["mine"]  # Ana's auto lock

    with ben:
        ben.join(SCENES)
        assert ben.switch()["ok"]  # the team has files: pulled first, then used
        mega = ben.root / "MEGA"
        assert tf._key(tf.junction_target(ben.root)) == tf._key(ben.copy())
        assert sorted(p.name for p in mega.iterdir()) == ["mesh.bin", "scene.json", "transforms.json"]
        stamp = json.loads(_read(mega / "transforms.json"))["base_stamp"]
        assert os.stat(mega / "scene.json").st_mtime_ns == stamp == MTIME  # the moves apply here too
        assert os.stat(mega / "mesh.bin").st_mtime_ns == MTIME + 700
        assert tf._sha_file(mega / "mesh.bin") == _sha(blob)
        # Ana holds MEGA: Ben's copy of it is read-only, so a plugin's save fails up front.
        assert ben.rows()["MEGA"]["state"] == "locked_by" and ben.rows()["MEGA"]["holder"] == "Ana"
        _write(mega / "scene.json.save.tmp", '{"name": "Ben"}')
        with pytest.raises(PermissionError):
            os.replace(mega / "scene.json.save.tmp", mega / "scene.json")
        os.unlink(mega / "scene.json.save.tmp")

    with ana:  # a move
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1, 2]}))
        assert ana.round(quiet_s=0)["pushed"] == 1
    with ben:
        before = repo.reload_epoch(PLUGIN)
        got = ben.round()
        assert got["tree"] == 1 and got["reload"] is True
        vcs_engine.after_pull(PLUGIN)
        assert repo.reload_epoch(PLUGIN) > before  # every process that loaded the plugin reloads it
        assert json.loads(_read(mega / "transforms.json"))["moves"] == [1, 2]
        assert os.stat(mega / "scene.json").st_mtime_ns == MTIME
        assert tf._readonly(mega / "transforms.json")  # still Ana's
        assert tf.scan(ben.account, SPACE, PLUGIN, ben.folder()).changed == {}  # nothing to send back


def test_a_unit_changed_on_both_pcs_is_held_then_kept_both_or_mine(env: Env, pcs: tuple[Machine, Machine],
                                                                   team: TeamService) -> None:
    from backend.uefn_plugins import spaces

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA", "Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        _write(ben.root / "Duel" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
        _write(ben.root / "MEGA" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
    with ana:
        _write(ana.root / "Duel" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 2000)
        _write(ana.root / "MEGA" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 2000)
        assert ana.round()["pushed"] == 2
    with ben:
        got = ben.round()
        assert got["conflicts"] == 2 and got["pushed"] == 0  # held, never pushed over theirs
        assert _read(ben.root / "Duel" / "scene.json") == '{"v": "ben"}'  # never overwritten
        assert {u: r["state"] for u, r in ben.rows().items()} == {"Duel": "conflict", "MEGA": "conflict"}
        st = spaces.scope_status(PLUGIN)
        assert sorted(c["unit"] for c in st["conflicts"]) == ["Duel", "MEGA"]
        diff = spaces.team_resolve(PLUGIN, "tree", "scenes/Duel", "compare")["diff"]
        assert "Changed here: 1 file(s)" in diff and "Changed by Ana: 1 file(s)" in diff
        assert ben.round()["pushed"] == 0
        # Keep both: Ben's Duel stays beside it as a unit of its own; Ana's lands.
        assert spaces.team_resolve(PLUGIN, "tree", "scenes/Duel", "both")["ok"]
        assert _read(ben.root / "Duel (Ben's copy)" / "scene.json") == '{"v": "ben"}'
        assert _read(ben.root / "Duel" / "scene.json") == '{"v": "ana"}'
        # Keep mine: Ben's MEGA goes out on top of Ana's.
        assert spaces.team_resolve(PLUGIN, "tree", "scenes/MEGA", "mine")["ok"]
        assert ben.round(quiet_s=0)["pushed"] == 2  # MEGA, and the kept copy as a new unit
        assert spaces.scope_status(PLUGIN)["conflicts"] == []
    with ana:
        ana.round()
        assert _read(ana.root / "MEGA" / "scene.json") == '{"v": "ben"}'
        assert _read(ana.root / "Duel (Ben's copy)" / "scene.json") == '{"v": "ben"}'
        assert _read(ana.root / "Duel" / "scene.json") == '{"v": "ana"}'


def test_take_theirs_drops_the_change_here(env: Env, pcs: tuple[Machine, Machine], team: TeamService) -> None:
    from backend.uefn_plugins import spaces

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        _write(ben.root / "Duel" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
        _write(ben.root / "Duel" / "extra.json", "{}", mtime_ns=MTIME + 1000)
    with ana:
        _write(ana.root / "Duel" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 2000)
        assert ana.round()["pushed"] == 1
    with ben:
        assert ben.round()["conflicts"] == 1
        assert spaces.team_resolve(PLUGIN, "tree", "scenes/Duel", "theirs")["ok"]
        assert _read(ben.root / "Duel" / "scene.json") == '{"v": "ana"}'
        assert not (ben.root / "Duel" / "extra.json").exists()  # only here: it goes
        assert ben.rows()["Duel"]["state"] == "up_to_date" and ben.round()["pushed"] == 0


def test_a_unit_changed_while_someone_holds_it_is_quarantined(env: Env, pcs: tuple[Machine, Machine],
                                                              team: TeamService) -> None:
    from backend.uefn_plugins import spaces, vcs_engine

    quarantine = {**SCENES, "on_locked": "quarantine"}
    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], quarantine)  # Ana's first change took MEGA's lock
    with ben:
        ben.join(quarantine)
        assert ben.switch()["ok"]
        assert not tf._readonly(ben.root / "MEGA" / "scene.json")  # quarantine: writes are allowed
        _write(ben.root / "MEGA" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
        seen = vcs_engine.watch(ben.account, SPACE, PLUGIN, ben.backend)
        assert seen["held"] == 1 and seen["quiet"] == 0
        assert ben.round(quiet_s=0)["pushed"] == 0 and team.head() == 1  # never sent
        row = ben.rows()["MEGA"]
        assert (row["state"], row["reason"]) == ("quarantined", "Changed here while Ana holds it")
        st = spaces.scope_status(PLUGIN)
        assert [o["state"] for o in st["changes"]["outgoing"] if o["kind"] == "tree"] == ["quarantined"]
        out = spaces.team_resolve(PLUGIN, "tree", "scenes/MEGA", "keep_copy")
        assert out["ok"] and Path(out["copy"]).name == "MEGA (Ben's copy)"
        ben.round(quiet_s=0)  # the team's MEGA comes back; the copy goes out as a unit of its own
        assert _read(ben.root / "MEGA" / "scene.json") == '{"name": "MEGA"}'
        assert _read(ben.root / "MEGA (Ben's copy)" / "scene.json") == '{"v": "ben"}'
        assert ben.rows()["MEGA"]["state"] == "locked_by" and team.content("MEGA (Ben's copy)/scene.json")


def test_store_json_merges_three_ways_between_pcs(env: Env, pcs: tuple[Machine, Machine], team: TeamService) -> None:
    design = {"id": "scenes", "root": "{LOCALAPPDATA}/UEFN-Ducky/scene-maker/scenes", "unit": "*", "lock": "none",
              "last": ["store.json"], "ignore": ["*/store.prev.json", "**/*.tmp"], "after_pull": "reload",
              "merge": ["store.json"]}
    ana, ben = pcs
    with ana:
        ana.join(design)
        _write(ana.root / "run1" / "store.json", json.dumps({"rooms": 1, "waves": 1}, indent=2), mtime_ns=MTIME)
        ana.switch()
        assert ana.switch(units=["run1"])["ok"]
    with ben:
        ben.join(design)
        assert ben.switch()["ok"]
        _write(ben.root / "run1" / "store.json", json.dumps({"rooms": 2, "waves": 1}, indent=2), mtime_ns=MTIME + 100)
    with ana:
        _write(ana.root / "run1" / "store.json", json.dumps({"rooms": 1, "waves": 3}, indent=2), mtime_ns=MTIME + 200)
        assert ana.round()["pushed"] == 1
    with ben:
        got = ben.round(quiet_s=0)
        assert got["conflicts"] == 0 and got["pushed"] == 1  # merged with Ana's, then sent
        assert json.loads(_read(ben.root / "run1" / "store.json")) == {"rooms": 2, "waves": 3}
    with ana:
        ana.round()
        assert json.loads(_read(ana.root / "run1" / "store.json")) == {"rooms": 2, "waves": 3}


def test_update_now_holds_a_visible_reload_plugins_changes(env: Env, pcs: tuple[Machine, Machine],
                                                           team: TeamService, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import panel_presence, spaces, vcs_engine

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
    with ana:
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1, 2]}))
        assert ana.round(quiet_s=0)["pushed"] == 1
    with ben:
        visible = {"on": True}
        real = panel_presence.panel_state
        monkeypatch.setattr(panel_presence, "panel_state",
                            lambda p: {**real(p), "visible": visible["on"]} if p == PLUGIN else real(p))
        got = ben.round()
        assert got["queued"] == 1 and got["tree"] == 0  # waits behind Update now
        transforms = ben.root / "MEGA" / "transforms.json"
        assert json.loads(_read(transforms))["moves"] == [1]
        assert tf._readonly(transforms)  # nobody edits a stale base meanwhile
        st = spaces.scope_status(PLUGIN)
        assert (st["changes"]["held"], st["changes"]["incoming"], st["changes"]["incomingBy"]) == (True, 1, ["Ana"])
        row = ben.rows()["MEGA"]
        assert row["state"] == "incoming" and row["unitId"].startswith("tree:scenes/")
        assert vcs_engine.folder_due(ben.account, SPACE, PLUGIN) is False  # on screen: it waits
        visible["on"] = False
        assert vcs_engine.folder_due(ben.account, SPACE, PLUGIN) is True  # hidden: a round lands it
        visible["on"] = True
        before = repo.reload_epoch(PLUGIN)
        out = vcs_engine.apply_waiting(PLUGIN)  # an agent's call lands it first
        assert out == {"ok": True, "applied": 1, "error": ""}
        assert json.loads(_read(transforms))["moves"] == [1, 2] and not tf._readonly(transforms)
        assert repo.reload_epoch(PLUGIN) > before
        st = spaces.scope_status(PLUGIN)
        assert st["changes"]["held"] is False and ben.rows()["MEGA"]["state"] == "up_to_date"


def test_the_bar_status_of_a_folder_plugin(env: Env, team: TeamService) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import spaces

    st = spaces.scope_status(PLUGIN)
    assert st["shareable"] and st["folderNote"] == tf.PLAINTEXT_NOTE and st["folderOffers"] == []
    assert [(f["id"], f["refused"], f["afterPull"]) for f in st["teamFolders"]] == [("scenes", "", "reload")]
    assert st["binding"] is None
    space_vcs.binding_put(PLUGIN, "Alpha Studio", 40)  # a switch is getting the team's files
    st = spaces.scope_status(PLUGIN)
    assert st["state"] == "binding" and st["binding"]["text"] == "Getting Alpha Studio's files… 40%"
    space_vcs.binding_put(PLUGIN, None)
    assert spaces.scope_status(PLUGIN)["binding"] is None


def test_the_background_sync_looks_at_the_folders_and_sends_a_quiet_unit(env: Env, pcs: tuple[Machine, Machine],
                                                                         team: TeamService,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import sync_scheduler, vcs_engine

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA"])
        looked: list[str] = []
        real_watch = vcs_engine.watch
        monkeypatch.setattr(vcs_engine, "watch", lambda *a, **k: looked.append(a[2]) or real_watch(*a, **k))
        pulled: list[str] = []
        monkeypatch.setattr(vcs_engine, "after_pull", pulled.append)
        sched = sync_scheduler.Scheduler(spawn=lambda fn: fn())
        sched.tick()  # the first look at the space: its round at start, and a look at the folders
        assert looked == [PLUGIN] and team.head() == 1
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": 2}', mtime_ns=MTIME + 3000)  # quiet already
        sched._folder_looks.clear()
        sched.tick(time.time() + 120)  # a look: the quiet unit wakes a round, which sends it
        sched.tick(time.time() + 240)
        assert team.content("MEGA/scene.json") == b'{"name": "MEGA", "v": 2}'
        assert pulled == []  # nothing came down: no reload


def test_the_agents_tools_switch_list_and_lock_folder_units(env: Env, pcs: tuple[Machine, Machine], team: TeamService,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tools.panel import panel_plugin_data as tools
    from backend.uefn_plugins import host

    monkeypatch.setattr(tools, "_confirm", lambda prompt, title="Confirm": True)
    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    ana, _ = pcs
    with ana:
        ana.join(SCENES)
        _write(ana.root / "MEGA" / "scene.json", '{"v": 1}', mtime_ns=MTIME)
        out = json.loads(tools.ducky_plugin_data_scope(PLUGIN, SPACE))  # an empty team: the bar picks units
        assert out["ok"] is False and out["needs"] == "first_share" and [u["unit"] for u in out["units"]] == ["MEGA"]
        assert json.loads(tools.ducky_team_status(PLUGIN))["scope"]["kind"] == "personal"  # nothing linked
        assert ana.switch(units=["MEGA"])["ok"]
        status = json.loads(tools.ducky_team_status(PLUGIN))
        assert status["folders"] == [{"unit": "scenes/MEGA", "name": "MEGA", "state": "up_to_date"}]
        got = json.loads(tools.ducky_team_lock(PLUGIN, "scenes/MEGA", "release"))  # Ana's auto lock
        assert got["ok"] and got["unit"].startswith("tree:scenes/")
        assert json.loads(tools.ducky_team_lock(PLUGIN, "scenes/MEGA", "acquire"))["state"] == "granted"
        out = json.loads(tools.ducky_plugin_data_scope(PLUGIN, "local"))  # back to Local: its folders too
        assert out["ok"] and tf._key(tf.junction_target(ana.root)) == tf._key(tf.local_copy(PLUGIN, ana.folder()))


# --------------------------------------------------------------------------- switches, projects, accounts


def test_a_switch_away_sends_a_changed_unit_before_the_junction_moves(env: Env, pcs: tuple[Machine, Machine],
                                                                      team: TeamService,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import host, spaces
    from frontend.ui_web.test_panel_api_store_scope import Api

    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    monkeypatch.setattr(spaces, "sync_active", lambda *a, **k: {"ok": True})
    ana, _ = pcs
    with ana:
        ana.join(SCENES)
        _write(ana.root / "MEGA" / "scene.json", '{"v": 1}', mtime_ns=MTIME)
        api = Api()
        asked = api.plugin_scope_set(PLUGIN, SPACE)  # the bar's switch: an empty team asks first
        assert asked["ok"] is False and asked["needs"] == "first_share" and asked["team"] == "Alpha Studio"
        assert api.plugin_scope_set(PLUGIN, SPACE, {"units": ["MEGA"]})["scope"]["kind"] == "shared"
        assert team.head() == 1
        _write(ana.root / "MEGA" / "scene.json", '{"v": 2}')  # just saved: not quiet yet
        heads: list[int] = []
        real = tf.repair
        monkeypatch.setattr(tf, "repair", lambda link, target: heads.append(team.head()) or real(link, target))
        out = api.plugin_scope_set(PLUGIN, "personal")
        assert out["scope"]["kind"] == "personal"
        assert heads == [2] and team.content("MEGA/scene.json") == b'{"v": 2}'  # sent before the re-point
        local = tf.local_copy(PLUGIN, ana.folder())
        assert tf._key(tf.junction_target(ana.root)) == tf._key(local)
        assert {e["type"] for e in api.pushed} == {"plugin_scope_changed"}


def test_a_project_switch_binds_the_new_projects_folder(env: Env, pcs: tuple[Machine, Machine], team: TeamService,
                                                        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading as th

    from backend.uefn_plugins import host, store
    from frontend.ui_web import project_switch

    design = {**DESIGN, "root": "{PROJECT}/.ducky/scene-maker"}
    ana, _ = pcs
    with ana:
        ana.join(design)
        one, two = _island(tmp_path, "Island"), _island(tmp_path, "Other", "aaaa-bbbb")
        open_project = {"root": str(one)}
        monkeypatch.setattr(tf, "current_project_root", lambda: open_project["root"])
        _write(one / ".ducky" / "scene-maker" / "store.json", '{"rooms": 1}', mtime_ns=MTIME)
        _write(two / ".ducky" / "scene-maker" / "store.json", '{"rooms": "local"}', mtime_ns=MTIME)
        ana.switch()
        assert ana.switch(units=[GUID.lower()])["ok"]
        folder = ana.folder()
        assert tf._key(tf.junction_target(one / ".ducky" / "scene-maker")) == \
            tf._key(tf.team_copy(ana.account, SPACE, PLUGIN, folder, GUID.lower()))
        # The team already has the other island's design (pulled here before).
        _write(tf.team_copy(ana.account, SPACE, PLUGIN, folder, "aaaa-bbbb") / "store.json", '{"rooms": 2}')
        restarted: list[str] = []
        monkeypatch.setattr(host, "reload_single_plugin", restarted.append)
        monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: [PLUGIN])
        open_project["root"] = str(two)
        project_switch.set_panel_project_root(str(two), push_ui=False)
        for t in th.enumerate():
            if t.name == "project-folders":
                t.join(30)
        link = two / ".ducky" / "scene-maker"
        assert tf._key(tf.junction_target(link)) == tf._key(tf.team_copy(ana.account, SPACE, PLUGIN, folder,
                                                                              "aaaa-bbbb"))
        assert json.loads(_read(link / "store.json")) == {"rooms": 2} and restarted == [PLUGIN]
        # Its own folder went into Local's copy for that project, untouched.
        assert _read(tf.local_copy(PLUGIN, folder, "aaaa-bbbb") / "store.json") == '{"rooms": "local"}'


def test_signing_out_points_team_folders_back_at_local(env: Env, pcs: tuple[Machine, Machine], team: TeamService,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import threading as th

    from backend.uefn_plugins import host, scopes, spaces, store

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA"])
        assert tf._key(tf.junction_target(ana.root)) == tf._key(ana.copy())
        account = ana.account
        restarted: list[str] = []
        monkeypatch.setattr(host, "reload_single_plugin", restarted.append)
        monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: [PLUGIN])
        env.key = ""  # signed out
        spaces.on_account_change(account, scopes.LOCAL)
        for t in th.enumerate():
            if t.name == "space-account-change":
                t.join(30)
        local = tf.local_copy(PLUGIN, ana.folder())
        assert tf._key(tf.junction_target(ana.root)) == tf._key(local)
        assert sorted(p.name for p in ana.root.iterdir()) == ["Duel", "MEGA"]  # Local's files
        assert restarted == [PLUGIN]


def test_links_made_before_folders_could_be_shared_go_back_to_local_once(env: Env, team: TeamService,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store.repos import plugin_data
    from backend.uefn_plugins import scopes, spaces, sync_scheduler, vcs_engine

    _scenes(env.root)
    account = env.account
    plugin_data.link_set(account, PLUGIN, SPACE)
    plugin_data.sync_put(account, SPACE, label="Alpha Studio")
    assert vcs_engine.migrate_folder_links() == [PLUGIN]
    assert scopes.active_scope(PLUGIN)["kind"] == "personal"
    st = spaces.scope_status(PLUGIN)
    assert st["folderOffers"] == [{"space": SPACE, "label": "Alpha Studio"}]
    assert not tf._is_junction(env.root)  # nothing moved: the plugin's own folder as it was
    assert vcs_engine.migrate_folder_links() == []
    # The background sync does it once per account, at its first look.
    runs: list[int] = []
    monkeypatch.setattr(sync_scheduler, "_migrate_folder_links", lambda: runs.append(1))
    sched = sync_scheduler.Scheduler(spawn=lambda fn: fn())
    sched._refresh(time.time(), spaces.provider_plugin())
    sched._refresh(time.time() + 60, spaces.provider_plugin())
    assert runs == [1]


# --------------------------------------------------------------------------- review fixes


def test_a_rename_by_case_only_is_the_same_file_and_never_a_delete(env: Env, pcs: tuple[Machine, Machine],
                                                                    team: TeamService) -> None:
    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA", "Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
    with ana:
        os.rename(ana.root / "MEGA" / "scene.json", ana.root / "MEGA" / "Scene.json")
        _write(ana.root / "MEGA" / "Scene.json", '{"name": "MEGA", "v": 2}', mtime_ns=MTIME + 3000)
        os.rename(ana.root / "Duel", ana.root / "DUEL")  # a unit renamed by case only: same files
        assert ana.round(quiet_s=0)["pushed"] == 1  # MEGA's edit only
        assert not [i for i in team.server.space(SPACE).repo(PLUGIN).items.values() if i.op == "del"]
        assert {r["relpath"] for r in repo.files(ana.account, SPACE, PLUGIN, "scenes")} == {
            "MEGA/Scene.json", "MEGA/transforms.json", "DUEL/scene.json"}
    with ben:
        assert ben.round()["tree"] == 1
        assert "scene.json" in [p.name for p in (ben.root / "MEGA").iterdir()]  # its name here stays
        assert _read(ben.root / "MEGA" / "scene.json") == '{"name": "MEGA", "v": 2}'
        assert len(repo.files(ben.account, SPACE, PLUGIN, "scenes")) == 3  # one row per file
        assert tf.scan(ben.account, SPACE, PLUGIN, ben.folder()).changed == {}
        assert ben.round(quiet_s=0)["pushed"] == 0  # no delete goes back to the team
    with ana:
        ana.round(quiet_s=0)
        assert (ana.root / "MEGA" / "Scene.json").is_file() and (ana.root / "DUEL" / "scene.json").is_file()
        assert team.content("MEGA/Scene.json") == b'{"name": "MEGA", "v": 2}'


def test_a_unit_with_a_file_that_fails_to_download_lands_whole_later(env: Env, pcs: tuple[Machine, Machine],
                                                                      team: TeamService) -> None:
    from backend.store.repos import space_vcs

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
    with ana:
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": 2}', mtime_ns=MTIME + 3000)
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME + 3000, "moves": [1, 2]}))
        assert ana.round(quiet_s=0)["pushed"] == 1
    bad = next(i for i in team.server.space(SPACE).repo(PLUGIN).items.values()
               if i.meta.get("relpath") == "MEGA/transforms.json")
    team.server.bad.add((SPACE, PLUGIN, "tree", bad.key, bad.seq))  # its download won't verify
    with ben:
        assert ben.round()["tree"] == 0
        assert json.loads(_read(ben.root / "MEGA" / "scene.json")) == {"name": "MEGA"}  # neither file landed
        assert json.loads(_read(ben.root / "MEGA" / "transforms.json"))["moves"] == [1]
        assert len(space_vcs.blocked(ben.account, SPACE, PLUGIN)) == 2  # both wait, to land together
        team.server.bad.clear()
        assert ben.round(now=time.time() + 3600)["tree"] == 1
        assert json.loads(_read(ben.root / "MEGA" / "scene.json"))["v"] == 2
        assert json.loads(_read(ben.root / "MEGA" / "transforms.json"))["moves"] == [1, 2]
        assert space_vcs.blocked(ben.account, SPACE, PLUGIN) == []


def test_take_theirs_and_keep_both_change_nothing_until_theirs_is_here(env: Env, pcs: tuple[Machine, Machine],
                                                                       team: TeamService) -> None:
    from backend.uefn_plugins import spaces

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        _write(ben.root / "Duel" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
    with ana:
        _write(ana.root / "Duel" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 2000)
        assert ana.round()["pushed"] == 1
    with ben:
        assert ben.round()["conflicts"] == 1
        team.server.offline = True
        for choice in ("theirs", "both"):
            out = spaces.team_resolve(PLUGIN, "tree", "scenes/Duel", choice)
            assert out["ok"] is False and "couldn't be downloaded" in out["error"]
            assert _read(ben.root / "Duel" / "scene.json") == '{"v": "ben"}'  # mine is still here
            assert not (ben.root / "Duel (Ben's copy)").exists()
            assert [c["unit"] for c in spaces.scope_status(PLUGIN)["conflicts"]] == ["Duel"]  # still to settle
        team.server.offline = False
        assert spaces.team_resolve(PLUGIN, "tree", "scenes/Duel", "both")["ok"]
        assert _read(ben.root / "Duel (Ben's copy)" / "scene.json") == '{"v": "ben"}'
        assert _read(ben.root / "Duel" / "scene.json") == '{"v": "ana"}'
        assert spaces.scope_status(PLUGIN)["conflicts"] == []


def test_adoption_waits_for_calls_and_catches_up_with_saves_made_while_it_copied(env: Env,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    _scenes(env.root)
    monkeypatch.setattr(tf, "_same_volume", lambda a, b: False)  # another drive: copied first, then swapped
    real_copy, real_into = tf._copy_tree, tf._adopt_into
    fenced: list[bool] = []

    def copy_then_saves(src: Path, dst: Path) -> dict[str, tuple[int, int]]:
        seen = real_copy(src, dst)
        _write(env.root / "MEGA" / "scene.json", '{"name": "MEGA", "saved": true}', mtime_ns=MTIME + 9000)
        _write(env.root / "New" / "scene.json", "{}")
        os.unlink(env.root / "notes.txt")
        return seen

    monkeypatch.setattr(tf, "_copy_tree", copy_then_saves)
    monkeypatch.setattr(tf, "_adopt_into", lambda root, local: fenced.append(tf.moving(PLUGIN)) or real_into(root, local))
    assert tf.adopt(PLUGIN, env.binding())["adopted"] == "copy"
    assert fenced == [True]  # calls that start meanwhile wait for it
    local = tf.local_copy(PLUGIN, env.folder())
    assert json.loads(_read(local / "MEGA" / "scene.json"))["saved"] and (local / "New" / "scene.json").is_file()
    assert not (local / "notes.txt").exists() and tf._is_junction(env.root)
    assert not [p for p in env.root.parent.iterdir() if tf.STAGING in p.name]
    assert not [p for p in local.parent.iterdir() if tf.ADOPTING in p.name]
    assert not tf.moving(PLUGIN)

    # A raw OS error refuses the switch with the reason, and the link row written first goes again.
    env.manifest({**SCENES, "root": "{LOCALAPPDATA}/UEFN-Ducky/other/scenes"}, plugin="other")
    _write(env.local / "UEFN-Ducky" / "other" / "scenes" / "A" / "scene.json", "{}")

    def denied(root: Path, local: Path) -> str:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(tf, "_adopt_into", denied)
    out = tf.switch("other", "spaceT", project_root="", pull=lambda: (True, ""))
    assert out["ok"] is False and "denied" in out["error"] and repo.link_get("other", "scenes") is None


def test_a_folder_the_plugin_makes_again_during_adoption_keeps_every_file(env: Env,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _scenes(env.root)
    real = tf._make_junction
    made: list[int] = []

    def plugin_saves_first(link: Path, target: Path) -> None:
        if not made:
            made.append(1)
            _write(Path(link) / "MEGA" / "scene.json", '{"defaults": true}')  # its folder, made again
        real(link, target)

    monkeypatch.setattr(tf, "_make_junction", plugin_saves_first)
    assert tf.adopt(PLUGIN, env.binding())["adopted"] == "rename"
    local = tf.local_copy(PLUGIN, env.folder())
    assert _read(local / "MEGA" / "scene.json") == '{"name": "MEGA"}'  # the real data stays
    assert _read(local / "MEGA" / "scene (conflict).json") == '{"defaults": true}'
    assert tf._is_junction(env.root) and repo.link_get(PLUGIN, "scenes")["root"] == str(env.root)


def test_a_move_cut_short_is_put_right(env: Env) -> None:
    _scenes(env.root)
    local = tf.local_copy(PLUGIN, env.folder())
    staging = env.root.with_name(f"scenes{tf.STAGING}dead1234")
    os.rename(env.root, staging)  # the app closed right after the folder moved aside
    tf._mkdirs(local.with_name(f"scenes{tf.ADOPTING}dead1234") / "MEGA")  # and a half-made copy
    repo.link_put(PLUGIN, "scenes", "", root=str(env.root), target=str(local), account="_pc", space="personal")
    assert tf.bind_plugin(PLUGIN, project_root="")["ok"]
    assert tf._key(tf.junction_target(env.root)) == tf._key(local)
    assert sorted(p.name for p in env.root.iterdir()) == ["Duel", "MEGA", "notes.txt"]
    assert not staging.exists() and not [p for p in local.parent.iterdir() if tf.ADOPTING in p.name]


def test_a_plugin_test_adopts_first_and_refuses_calls_from_outside_it(env: Env) -> None:
    import contextvars

    from backend.uefn_plugins import scopes

    _scenes(env.root)  # never shared: still the plugin's own folder
    scope = scopes.sandbox_begin(PLUGIN)
    local = tf.local_copy(PLUGIN, env.folder())
    try:
        assert scope["id"] in tf.junction_target(env.root) and not any(env.root.iterdir())
        assert (local / "MEGA" / "scene.json").is_file()  # the real files wait in the Local copy
        with scopes.write_group(PLUGIN):  # the test's own call
            pass

        def outside() -> None:
            with scopes.write_group(PLUGIN, actor="agent"):
                pass

        with pytest.raises(ValueError, match="A test of Scene Maker is running"):
            contextvars.Context().run(outside)
        with pytest.raises(ValueError, match="its other calls are refused"):
            scopes.sandbox_begin(PLUGIN)
    finally:
        scopes.sandbox_end(PLUGIN)
    assert tf._key(tf.junction_target(env.root)) == tf._key(local) and (env.root / "MEGA" / "scene.json").is_file()
    with scopes.write_group(PLUGIN):  # calls go through again
        pass


def test_a_call_that_starts_during_a_repoint_waits_for_it(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenes(env.root)
    tf.adopt(PLUGIN, env.binding())
    _write(env.team() / "Arena" / "scene.json", "{}")
    order: list[Any] = []
    threads: list[threading.Thread] = []
    real = tf.repair

    def call() -> None:
        with tf.call_marker(PLUGIN):
            order.append(("call", repo.reload_epoch(PLUGIN)))

    def slow(link: Path, target: Path) -> str:
        threads.append(threading.Thread(target=call))
        threads[-1].start()
        time.sleep(0.3)
        order.append("repointing")
        return real(link, target)

    monkeypatch.setattr(tf, "repair", slow)
    before = repo.reload_epoch(PLUGIN)
    assert tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""))["ok"]
    threads[0].join(10)
    assert order == ["repointing", ("call", before + 1)]  # after the move, on the new epoch


def test_a_panel_call_reloads_a_plugin_whose_folders_moved_first(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import host

    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: True)
    calls: list[Any] = []
    monkeypatch.setitem(host._PANEL_RPC, PLUGIN, {"save": lambda: calls.append("old")})
    monkeypatch.setitem(host._FOLDER_EPOCH, PLUGIN, repo.reload_epoch(PLUGIN))

    def reloaded(pid: str) -> bool:
        calls.append(("reload", len(repo.calls(pid))))  # its call marker is down already
        host._PANEL_RPC[pid] = {"save": lambda: calls.append("new")}
        return True

    monkeypatch.setattr(host, "reload_if_folders_moved", reloaded)
    host.call_panel_rpc(PLUGIN, "save")
    assert calls == [("reload", 1), "new"]


def test_own_commit_coming_back_after_a_gap_is_no_conflict(env: Env, pcs: tuple[Machine, Machine],
                                                           team: TeamService) -> None:
    from backend.uefn_plugins import vcs_engine

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA", "Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        _write(ben.root / "Duel" / "scene.json", '{"v": "ben"}', mtime_ns=MTIME + 1000)
        assert ben.round(quiet_s=0)["pushed"] == 1 and team.head() == 3  # Ana hasn't pulled it
    with ana:
        from backend.store.repos import space_vcs

        mega = ana.rows()["MEGA"]["unitId"]
        sent = space_vcs.unit_seqs(ana.account, SPACE, PLUGIN, [mega])
        _write(ana.root / "MEGA" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 4000)
        assert ana.round(pull=False, quiet_s=0)["pushed"] == 1 and team.head() == 4  # past the gap
        # Not claimed at 4 yet (what a lock asks with): the gap may hold a change of MEGA.
        assert space_vcs.unit_seqs(ana.account, SPACE, PLUGIN, [mega]) == sent and sent[mega] < 3
        _write(ana.root / "MEGA" / "scene.json", '{"v": "ana, again"}', mtime_ns=MTIME + 5000)
        vcs_engine.watch(ana.account, SPACE, PLUGIN, ana.backend)  # seen as a change on top of seq 4
        got = ana.round(push=False)  # Ben's seq 3, then this PC's own seq 4
        assert got["conflicts"] == 0 and ana.rows()["MEGA"]["state"] == "changed_here"
        assert space_vcs.unit_seqs(ana.account, SPACE, PLUGIN, [mega]) == {mega: 4}
        assert _read(ana.root / "MEGA" / "scene.json") == '{"v": "ana, again"}'
        assert _read(ana.root / "Duel" / "scene.json") == '{"v": "ben"}'
        assert ana.round(quiet_s=0)["pushed"] == 1 and team.content("MEGA/scene.json") == b'{"v": "ana, again"}'


def test_a_save_while_a_unit_lands_is_never_lost(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "MEGA/transforms.json": '{"t": 1}'})
    team, folder = env.team(), env.folder()
    real = tf._dirty_here

    def then_a_save(*a: Any, **k: Any) -> set[str]:
        out = real(*a, **k)
        _write(team / "MEGA" / "scene.json", '{"v": "saved just now"}')  # after the check, before the replace
        return out

    monkeypatch.setattr(tf, "_dirty_here", then_a_save)
    items = [_incoming(env, "MEGA/scene.json", '{"v": 2}'), _incoming(env, "MEGA/transforms.json", '{"t": 2}')]
    out = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA", items, seq=2)
    assert out["state"] == "conflict" and "MEGA/scene.json" in out["mine"]
    assert _read(team / "MEGA" / "scene.json") == '{"v": "saved just now"}'
    assert _read(team / "MEGA" / "transforms.json") == '{"t": 1}'  # the unit went back whole
    assert not [p for p in (team / "MEGA").iterdir() if p.name.endswith(".tmp")]
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["sync_state"] == "conflict"


def test_what_a_crash_leaves_in_a_copy_is_put_right(env: Env) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}', "Duel/scene.json": '{"v": 1}'})
    team, folder = env.team(), env.folder()
    os.replace(team / "MEGA" / "scene.json", team / "MEGA" / f"scene.json{tf.BAK_TAG}1234abcd.tmp")  # moved aside
    _write(team / "Duel" / f".scene.json{tf.IN_TAG}5678.tmp", "{")  # a download that never landed
    out = tf.scan(env.account, "spaceT", PLUGIN, folder, now=time.time() + 10)
    assert out.changed == {} and out.tmp == set() and out.waiting == [] and out.quiet == []
    assert _read(team / "MEGA" / "scene.json") == '{"v": 1}'
    assert sorted(p.name for p in (team / "Duel").iterdir()) == ["scene.json"]
    # Files that landed before a crash rolled their rows back: the same change lands again, no conflict.
    _write(team / "MEGA" / "scene.json", '{"v": 2}', mtime_ns=MTIME + 100)
    again = tf.apply_unit(env.account, "spaceT", PLUGIN, folder, "MEGA",
                          [_incoming(env, "MEGA/scene.json", '{"v": 2}', mtime_ns=MTIME + 100)], seq=2)
    assert again["ok"]
    row = repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")
    assert (row["dirty"], row["base_seq"]) == (0, 2)


def test_a_project_switch_tells_the_plugin_what_its_pull_landed(env: Env, team: TeamService, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store.repos import plugin_data
    from backend.uefn_plugins import host, spaces, vcs_engine

    env.manifest(DESIGN)
    one = _island(tmp_path, "Island")
    monkeypatch.setattr(tf, "current_project_root", lambda: str(one))
    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    monkeypatch.setattr(spaces, "ledger_for", lambda *a: object())
    plugin_data.link_set(env.account, PLUGIN, SPACE)
    plugin_data.sync_put(env.account, SPACE, label="Alpha Studio")
    repo.copy_put(env.account, SPACE, PLUGIN, "design", "", pulled_at=time.time())
    told: list[str] = []
    monkeypatch.setattr(vcs_engine, "after_pull", told.append)
    answer = {"ok": False}

    def pull_copy(account: str, space: str, plugin: str, backend: Any, *, landed: dict[str, int], **kw: Any) -> Any:
        landed["tree"] += 1  # a unit landed before it stopped
        return (True, "") if answer["ok"] else (False, "Alpha Studio is offline.")

    monkeypatch.setattr(vcs_engine, "pull_copy", pull_copy)
    spaces.rebind_project_folders(str(one), [PLUGIN])
    assert told == [PLUGIN]  # the plugin hears it (a reload plugin restarts)
    copy = repo.copy_get(env.account, SPACE, PLUGIN, "design", GUID.lower())
    assert (copy["ready"], copy["offer"]) == (0, 0)  # a pull that didn't finish says nothing of the team's copy
    answer["ok"] = True
    spaces.rebind_project_folders(str(one), [PLUGIN])
    assert repo.copy_get(env.account, SPACE, PLUGIN, "design", GUID.lower())["offer"] == 1


def test_core_reads_a_file_the_plugin_can_still_move_or_delete(env: Env) -> None:
    team = env.team()
    target = _write(team / "MEGA" / "scene.json", "old")
    _write(team / "MEGA" / "scene.json.save.tmp", "new")
    with tf.open_read(target) as f:  # Python's open() would make each of these fail (in use)
        assert f.read(1) == b"o"
        os.rename(target, target.with_name("scene.json.bak"))  # a save that moves the old file aside
        os.rename(team / "MEGA" / "scene.json.save.tmp", target)
        os.unlink(target.with_name("scene.json.bak"))
        assert f.read() == b"ld"  # core reads what it opened
    assert _read(target) == "new" and tf._sha_file(target) == _sha("new")
    with pytest.raises(FileNotFoundError):
        tf.open_read(team / "nope.json")


def test_what_goes_out_is_a_copy_made_as_the_scan_saw_it(env: Env, pcs: tuple[Machine, Machine], team: TeamService,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import vcs_engine

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
        target = ana.root / "MEGA" / "scene.json"
        real_push = ana.backend.push
        paths: list[str] = []

        def push(space: str, plugin: str, cs: Any) -> Any:  # the plugin saves again while the upload runs
            paths.extend(it.path for it in cs.items if it.op == "put")
            _write(target.with_name("scene.json.save.tmp"), '{"v": 3}')
            os.replace(target.with_name("scene.json.save.tmp"), target)
            return real_push(space, plugin, cs)

        monkeypatch.setattr(ana.backend, "push", push)
        _write(target, '{"v": 2}', mtime_ns=MTIME + 3000)
        assert ana.round(quiet_s=0)["pushed"] == 1
        assert paths and all("outgoing" in p for p in paths)
        item = next(i for i in team.server.space(SPACE).repo(PLUGIN).items.values()
                    if i.meta.get("relpath") == "MEGA/scene.json")
        assert item.content == b'{"v": 2}' and item.sha256 == _sha('{"v": 2}')  # as the changeset says
        assert not any(tf.outbox(ana.account, SPACE, PLUGIN).iterdir())
        monkeypatch.setattr(ana.backend, "push", real_push)
        # Changed between the scan and the upload: the unit goes again as it is now.
        real_path = vcs_engine._tree_path
        changed: list[int] = []

        def saved_after_scan(ctx: Any, snap: Any) -> str:
            if not changed:
                changed.append(1)
                _write(target, '{"v": 4}', mtime_ns=MTIME + 9000)
            return real_path(ctx, snap)

        monkeypatch.setattr(vcs_engine, "_tree_path", saved_after_scan)
        assert ana.round(quiet_s=0)["pushed"] == 1 and team.content("MEGA/scene.json") == b'{"v": 4}'


def test_two_copies_of_one_island_never_share_a_folder_copy(env: Env, tmp_path: Path) -> None:
    env.manifest(DESIGN, plugin="rogue")
    one, dup = _island(tmp_path, "Island"), _island(tmp_path, "Island copy")  # a copied island keeps its id
    _write(one / ".ducky" / "roguelike-design" / "store.json", '{"rooms": 1}')
    _write(dup / ".ducky" / "roguelike-design" / "store.json", '{"rooms": 0}')
    tf.adopt("rogue", env.binding("rogue", str(one)))
    b = env.binding("rogue", str(dup))
    assert b.refused == tf.REFUSE_SAME_ISLAND.format(name="Island")
    with pytest.raises(tf.FolderError, match="same island"):
        tf.adopt("rogue", b)
    out = tf.switch("rogue", "spaceT", project_root=str(dup), pull=lambda: (True, ""))
    assert out["ok"] is False and "same island" in out["error"]
    assert tf.bind_plugin("rogue", project_root=str(dup))["folders"][0]["state"] == "refused"
    pc = tf.local_copy("rogue", env.folder("rogue"), GUID.lower())
    assert _read(pc / "store.json") == '{"rooms": 1}'  # the first island's design is untouched
    assert not tf._is_junction(dup / ".ducky" / "roguelike-design")
    assert _read(dup / ".ducky" / "roguelike-design" / "store.json") == '{"rooms": 0}'
    assert repo.link_get("rogue", "design", GUID.lower())["root"] == str(one / ".ducky" / "roguelike-design")


def test_a_read_only_mark_that_fails_to_save_leaves_the_file_writable(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _synced(env, {"MEGA/scene.json": '{"v": 1}'})
    team, folder = env.team(), env.folder()
    real = repo.file_put

    def failing(conn: Any, *a: Any, **k: Any) -> None:
        if k.get("readonly"):
            raise RuntimeError("disk I/O error")  # the row can't be saved
        real(conn, *a, **k)

    monkeypatch.setattr(repo, "file_put", failing)
    with pytest.raises(RuntimeError):
        tf.protect(env.account, "spaceT", PLUGIN, folder, ["MEGA"], True)
    assert not tf._readonly(team / "MEGA" / "scene.json")  # never read-only without its row saying so
    monkeypatch.setattr(repo, "file_put", real)
    assert tf.protect(env.account, "spaceT", PLUGIN, folder, ["MEGA"], True) == 1 and tf._readonly(team / "MEGA" /
                                                                                                    "scene.json")
    assert tf.protect(env.account, "spaceT", PLUGIN, folder, ["MEGA"], False) == 1
    assert not tf._readonly(team / "MEGA" / "scene.json")
    assert repo.file_get(env.account, "spaceT", PLUGIN, "scenes", "MEGA/scene.json")["readonly"] == 0


def test_a_space_reset_uploads_or_clears_the_folder_copies_too(env: Env, pcs: tuple[Machine, Machine],
                                                               team: TeamService) -> None:
    from backend.store.repos import plugin_data, space_vcs
    from backend.uefn_plugins import spaces

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
        plugin_data.sync_put(ana.account, SPACE, state="reset")
        space_vcs.reset_epoch(ana.account, SPACE, "e2")
        out = spaces.settle_reset(SPACE, "upload")
        rows = repo.files(ana.account, SPACE, PLUGIN, "scenes")
        assert rows and out["queued"] >= len(rows)
        assert {(r["dirty"], r["base_seq"], r["base_sha"], r["sync_state"]) for r in rows} == {(1, 0, "", "")}
        # Clear my copy: its files go (read-only ones too), the junction still points at the empty copy.
        plugin_data.sync_put(ana.account, SPACE, state="reset")
        tf.protect(ana.account, SPACE, PLUGIN, ana.folder(), ["MEGA"], True)
        spaces.settle_reset(SPACE, "clear")
        assert tf._key(tf.junction_target(ana.root)) == tf._key(ana.copy()) and not any(ana.root.iterdir())
        assert repo.files(ana.account, SPACE, PLUGIN, "scenes") == []
        assert repo.copy_get(ana.account, SPACE, PLUGIN, "scenes")["ready"] == 1
        ana.round()  # the next pull fills the copy again
        assert _read(ana.root / "MEGA" / "scene.json") == '{"name": "MEGA"}'


def test_copy_local_into_a_team_that_has_files_asks_which_units(env: Env) -> None:
    _scenes(env.root)
    _write(env.team() / "Duel" / "scene.json", '{"theirs": true}')
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""), first_share={"pick": True})
    assert out["needs"] == "first_share" and out["partial"] is True
    assert [u["unit"] for u in out["units"]] == ["MEGA", "notes.txt"]  # what the team lacks
    pushed: list[int] = []
    out = tf.switch(PLUGIN, "spaceT", project_root="", pull=lambda: (True, ""),
                    push=lambda: pushed.append(1) or (True, ""), first_share={"units": ["MEGA"]})
    assert out["ok"] and pushed == [1]
    assert sorted(p.name for p in env.root.iterdir()) == ["Duel", "MEGA"]
    assert _read(env.root / "Duel" / "scene.json") == '{"theirs": true}'


def test_a_project_the_team_has_no_copy_of_is_offered_on_that_teams_bar(env: Env, pcs: tuple[Machine, Machine],
                                                                        team: TeamService, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins import host, spaces
    from frontend.ui_web.test_panel_api_store_scope import Api

    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    monkeypatch.setattr(spaces, "sync_active", lambda *a, **k: {"ok": True})
    design = {**DESIGN, "root": "{PROJECT}/.ducky/scene-maker"}
    ana, _ = pcs
    with ana:
        ana.join(design)
        one, two = _island(tmp_path, "Island"), _island(tmp_path, "Other", "aaaa-bbbb")
        open_project = {"root": str(one)}
        monkeypatch.setattr(tf, "current_project_root", lambda: open_project["root"])
        _write(one / ".ducky" / "scene-maker" / "store.json", '{"rooms": 1}', mtime_ns=MTIME)
        _write(two / ".ducky" / "scene-maker" / "store.json", '{"rooms": 2}', mtime_ns=MTIME)
        ana.switch()
        assert ana.switch(units=[GUID.lower()])["ok"]
        open_project["root"] = str(two)
        spaces.rebind_project_folders(str(two), [PLUGIN])
        assert tf.offers(PLUGIN) == [{"space": SPACE, "label": "Alpha Studio"}]
        api = Api()
        asked = api.plugin_scope_set(PLUGIN, SPACE)  # the plugin is on Alpha already
        assert asked["needs"] == "first_share" and [u["unit"] for u in asked["units"]] == ["aaaa-bbbb"]
        assert api.plugin_scope_set(PLUGIN, SPACE, {"units": ["aaaa-bbbb"]})["scope"]["kind"] == "shared"
        assert tf._key(tf.junction_target(two / ".ducky" / "scene-maker")) == \
            tf._key(tf.team_copy(ana.account, SPACE, PLUGIN, ana.folder(), "aaaa-bbbb"))
        assert tf.offers(PLUGIN) == [] and team.content("aaaa-bbbb/store.json") == b'{"rooms": 2}'


def test_losing_a_space_saves_folder_units_never_sent_to_local(env: Env, pcs: tuple[Machine, Machine],
                                                               team: TeamService) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import scopes, vcs_engine

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
        _write(ana.root / "MEGA" / "scene.json", '{"v": "unsent"}', mtime_ns=MTIME + 3000)
        tf.scan(ana.account, SPACE, PLUGIN, ana.folder())
        assert space_vcs.pending_unsent(ana.account, SPACE) == 1
        out = vcs_engine.save_unsent_to_local(ana.account, SPACE, label="Alpha Studio")
        assert [s["key"] for s in out["saved"] if s["kind"] == "tree"] == ["scenes/MEGA"]
        local = tf.local_copy(PLUGIN, ana.folder())
        assert _read(local / "MEGA (from Alpha Studio)" / "scene.json") == '{"v": "unsent"}'
        assert _read(local / "MEGA" / "scene.json") == '{"name": "MEGA"}'  # Local's own MEGA stays
        assert space_vcs.pending_unsent(ana.account, SPACE) == 0
        scopes.purge_space(ana.account, SPACE)
        assert _read(ana.root / "MEGA (from Alpha Studio)" / "scene.json") == '{"v": "unsent"}'


def test_going_local_takes_every_projects_folder_back_to_local(env: Env, pcs: tuple[Machine, Machine],
                                                               team: TeamService, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    design = {**DESIGN, "root": "{PROJECT}/.ducky/scene-maker"}
    ana, _ = pcs
    with ana:
        ana.join(design)
        one, two = _island(tmp_path, "Island"), _island(tmp_path, "Other", "aaaa-bbbb")
        open_project = {"root": str(one)}
        monkeypatch.setattr(tf, "current_project_root", lambda: open_project["root"])
        _write(one / ".ducky" / "scene-maker" / "store.json", '{"rooms": 1}', mtime_ns=MTIME)
        _write(two / ".ducky" / "scene-maker" / "store.json", '{"rooms": 2}', mtime_ns=MTIME)
        ana.switch()
        assert ana.switch(units=[GUID.lower()])["ok"]
        open_project["root"] = str(two)
        assert ana.switch(units=["aaaa-bbbb"])["ok"]
        assert tf.switch(PLUGIN, "personal")["ok"]  # with the other island open
        for root, pid in ((one, GUID.lower()), (two, "aaaa-bbbb")):
            link = root / ".ducky" / "scene-maker"
            assert tf._key(tf.junction_target(link)) == tf._key(tf.local_copy(PLUGIN, ana.folder(), pid))
            assert repo.link_get(PLUGIN, "design", pid)["space_id"] == "personal"


def test_a_unit_the_server_keeps_failing_waits_alone_and_other_units_go(env: Env, pcs: tuple[Machine, Machine],
                                                                       team: TeamService) -> None:
    from backend.store.repos import plugin_data, space_vcs
    from backend.uefn_plugins import vcs_engine
    from backend.uefn_plugins.ledger import Error
    from frontend.ui_web import panel_api  # noqa: F401  (panel_api_store loads with it)
    from frontend.ui_web import panel_api_store

    ana, _ = pcs
    with ana:
        _share_scenes(ana, ["MEGA", "Duel"])
        inner, tries = ana.backend, []

        class NoMega:  # the server never takes a changeset of MEGA
            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

            def push(self, space: str, plugin: str, cs: Any) -> Any:
                if any(i.meta.get("unit_name") == "MEGA" for i in cs.items):
                    tries.append(cs.cs_id)
                    return Error("bad request")
                return inner.push(space, plugin, cs)

        def waits_over() -> None:  # as if MEGA's backoff had passed
            left = space_vcs.unit_tries(ana.account, SPACE, PLUGIN)
            space_vcs._state_put(f"{space_vcs.UNIT_TRIES_PREFIX}{ana.account}:{SPACE}:{PLUGIN}",
                                 {u: {**t, "next_try": 0.0} for u, t in left.items()} or None)

        def mega() -> dict[str, Any]:
            return next(o for o in vcs_engine.folder_outgoing(ana.account, SPACE, PLUGIN) if o["unit"] == "MEGA")

        team.clients[ana.account] = NoMega()
        head = team.head()
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": 2}', mtime_ns=MTIME + 3000)
        out = ana.round(quiet_s=0)
        # Alone, its error may be the server refusing everything: no try counts, the plugin backs off.
        assert (out["state"], len(tries), team.head()) == ("error", 1, head)
        assert space_vcs.head_get(ana.account, SPACE, PLUGIN)["next_try"] > time.time()
        assert (mega()["state"], mega()["attempts"]) == ("sending", 0)
        n = 2

        def with_duel() -> dict[str, Any]:  # another unit changes and goes in the same round
            nonlocal n
            n += 1
            _write(ana.root / "Duel" / "scene.json", json.dumps({"v": n}), mtime_ns=MTIME + 3000 + n * 1000)
            return ana.round(quiet_s=0)

        out = with_duel()  # Duel lands: MEGA's error is its own; it waits, the plugin doesn't back off
        assert (out["state"], out["pushed"], len(tries)) == ("ok", 1, 2)
        assert team.content("Duel/scene.json") == b'{"v": 3}'
        assert space_vcs.head_get(ana.account, SPACE, PLUGIN)["next_try"] == 0.0
        assert (mega()["state"], mega()["attempts"], mega()["reason"]) == ("queued", 1, "bad request")
        assert vcs_engine.folder_work(ana.account, SPACE, PLUGIN) is False  # nothing to do while it waits
        for k in range(2, plugin_data.BLOCK_AFTER + 1):
            waits_over()
            with_duel()
            assert len(tries) == k + 1
        assert (mega()["state"], mega()["reason"]) == ("blocked", "bad request")
        with_duel()
        assert len(tries) == plugin_data.BLOCK_AFTER + 1  # held: Retry, or a change, sends it again
        assert panel_api_store._retry_held(PLUGIN, "scenes/MEGA") == {"ok": True, "retried": 1}  # the drawer's Retry
        assert ana.round(quiet_s=0)["state"] == "error"  # alone again: no try counts
        assert len(tries) == plugin_data.BLOCK_AFTER + 2 and mega()["attempts"] == 0
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": 3}', mtime_ns=MTIME + 4000)
        ana.round(quiet_s=0)  # changed again: it goes at once, as it is now
        assert len(tries) == plugin_data.BLOCK_AFTER + 3
        team.clients[ana.account] = inner  # the server takes it now
        assert ana.round(quiet_s=0)["pushed"] == 1 and team.content("MEGA/scene.json") == b'{"name": "MEGA", "v": 3}'
        assert ana.rows()["MEGA"]["state"] == "up_to_date"
        assert space_vcs.unit_tries(ana.account, SPACE, PLUGIN) == {}


def test_a_lock_on_a_unit_with_a_file_set_aside_waits_until_the_unit_lands_whole(
        env: Env, pcs: tuple[Machine, Machine], team: TeamService) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA", "Duel"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        mega = ben.rows()["MEGA"]["unitId"]
    with ana:
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1, 2]}))
        assert ana.round(quiet_s=0)["pushed"] == 1
    bad = next(i for i in team.server.space(SPACE).repo(PLUGIN).items.values()
               if i.meta.get("relpath") == "MEGA/transforms.json")
    team.server.bad.add((SPACE, PLUGIN, "tree", bad.key, bad.seq))  # its download won't verify
    with ben:
        assert ben.round(quiet_s=0)["tree"] == 0 and len(space_vcs.blocked(ben.account, SPACE, PLUGIN)) == 1
    with ana:
        _write(ana.root / "Duel" / "scene.json", '{"v": "ana"}', mtime_ns=MTIME + 2000)
        assert ana.round(quiet_s=0)["pushed"] == 1  # a gap for Ben
    with ben:
        _write(ben.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": "ben"}', mtime_ns=MTIME + 3000)
        assert ben.round(pull=False, quiet_s=0)["pushed"] == 1  # another file of MEGA, past the gap
        top = team.head()
        assert ben.round(push=False)["state"] == "ok"  # Duel, then Ben's own commit comes back
        assert space_vcs.head_get(ben.account, SPACE, PLUGIN)["seq"] == top
        assert space_vcs.unit_seqs(ben.account, SPACE, PLUGIN, [mega])[mega] < bad.seq  # never claimed past it
        got = vcs_engine.acquire(ben.account, SPACE, PLUGIN, ben.backend, [(mega, "MEGA")])
        assert got[mega]["state"] == "behind"  # transforms.json isn't here: no lock on a stale MEGA
        team.server.bad.clear()
        got = vcs_engine.acquire(ben.account, SPACE, PLUGIN, ben.backend, [(mega, "MEGA")])
        assert got[mega]["state"] == "granted"  # it landed whole: claimed at the head, past Ben's own commit
        assert json.loads(_read(ben.root / "MEGA" / "transforms.json"))["moves"] == [1, 2]
        assert space_vcs.unit_seqs(ben.account, SPACE, PLUGIN, [mega]) == {mega: top}


def test_an_own_commit_while_a_file_of_its_unit_is_set_aside_is_claimed_once_the_unit_lands(
        env: Env, pcs: tuple[Machine, Machine], team: TeamService) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        mega = ben.rows()["MEGA"]["unitId"]
    with ana:
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME, "moves": [1, 2]}))
        assert ana.round(quiet_s=0)["pushed"] == 1
    bad = next(i for i in team.server.space(SPACE).repo(PLUGIN).items.values()
               if i.meta.get("relpath") == "MEGA/transforms.json")
    team.server.bad.add((SPACE, PLUGIN, "tree", bad.key, bad.seq))
    with ben:
        assert ben.round(quiet_s=0)["tree"] == 0
        before = space_vcs.unit_seqs(ben.account, SPACE, PLUGIN, [mega])[mega]
        _write(ben.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": "ben"}', mtime_ns=MTIME + 3000)
        assert ben.round(quiet_s=0)["pushed"] == 1
        top = team.head()
        assert space_vcs.head_get(ben.account, SPACE, PLUGIN)["seq"] == top  # the next seq: the head moved
        assert space_vcs.unit_seqs(ben.account, SPACE, PLUGIN, [mega]) == {mega: before}  # but MEGA isn't whole
        team.server.bad.clear()
        got = vcs_engine.acquire(ben.account, SPACE, PLUGIN, ben.backend, [(mega, "MEGA")])
        assert got[mega]["state"] == "granted"  # never "behind" for good: the commit skipped is claimed now
        assert space_vcs.unit_seqs(ben.account, SPACE, PLUGIN, [mega]) == {mega: top}


def test_a_big_units_later_part_that_fails_goes_again_as_it_was_so_its_group_closes(
        env: Env, pcs: tuple[Machine, Machine], team: TeamService, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine
    from backend.uefn_plugins.ledger import Error

    monkeypatch.setattr(vcs_engine, "MAX_ITEMS", 1)  # each file a part of the unit's group
    ana, ben = pcs
    with ana:
        _share_scenes(ana, ["MEGA"], LOOSE)
        inner, failed, shared = ana.backend, [], team.head()

        class Part2FailsOnce:
            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

            def push(self, space: str, plugin: str, cs: Any) -> Any:
                if cs.part == 2 and not failed:
                    failed.append(cs.cs_id)
                    return Error("bad request")
                return inner.push(space, plugin, cs)

        team.clients[ana.account] = Part2FailsOnce()
        _write(ana.root / "MEGA" / "scene.json", '{"name": "MEGA", "v": 2}', mtime_ns=MTIME + 3000)
        _write(ana.root / "MEGA" / "transforms.json", json.dumps({"base_stamp": MTIME + 3000, "moves": [1, 2]}))
        _write(ana.root / "MEGA" / "notes.txt", "new")
        out = ana.round(quiet_s=0)
        assert (out["state"], out["pushed"], len(failed)) == ("error", 1, 1)  # part 1 landed: part 2 backs off
        assert [(cs["part"], cs["state"]) for cs in space_vcs.cs_open(ana.account, SPACE)] == [
            (2, "pushing"), (3, "pending")]
        team.clients[ana.account] = inner
        assert ana.round(quiet_s=0)["state"] == "ok" and space_vcs.cs_open(ana.account, SPACE) == []
    unit = [cs for s, cs in sorted(team.server.space(SPACE).repo(PLUGIN).changesets.items()) if s > shared]
    assert [(cs.part, cs.last) for cs in unit] == [(1, False), (2, False), (3, True)]
    assert len({cs.group for cs in unit}) == 1  # one group, and it closed
    with ben:
        ben.join(LOOSE)
        assert ben.switch()["ok"]
        assert _read(ben.root / "MEGA" / "notes.txt") == "new"
        assert json.loads(_read(ben.root / "MEGA" / "scene.json"))["v"] == 2


def test_an_ais_own_file_edit_in_a_team_folder_goes_out_as_the_ais(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code's Edit or Codex's apply_patch on a plugin's shared folder is the AI's
    change, not the person's, even with no plugin call around it."""
    from backend.uefn_plugins import store

    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: [PLUGIN])
    env.root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    assert tf._caller(PLUGIN, now) == ("user", "")
    assert tf.note_ai_edit([env.tmp / "elsewhere.json"], ducky="Builder") == []
    assert tf.note_ai_edit([env.root / "arena.json"], ducky="Builder", at=now) == [PLUGIN]
    assert tf._caller(PLUGIN, now + 1) == ("agent", "Builder")
    assert tf._caller(PLUGIN, now + tf.AGENT_WINDOW_S + 1) == ("user", "")  # long after: the person again


def test_a_switch_whose_pull_lands_nothing_stops_soon_instead_of_spinning(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A teammate's upload cut off before its last part holds every changeset after it: the
    switch's pull lands nothing, so it stops with a reason rather than call the server for 10 minutes."""
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import vcs_engine

    head = {"seq": 5, "remote_seq": 9}
    runs: list[int] = []
    monkeypatch.setattr(vcs_engine, "SWITCH_STALL_S", 0.05)
    monkeypatch.setattr(vcs_engine, "SWITCH_RETRY_S", 0.01)
    monkeypatch.setattr(vcs_engine, "refresh", lambda *a, **k: {"state": "ok"})
    monkeypatch.setattr(vcs_engine, "run", lambda *a, **k: runs.append(1) or {"state": "ok", "tree": 0})
    monkeypatch.setattr(space_vcs, "head_get", lambda *a: dict(head))
    ok, why = vcs_engine.pull_copy(env.account, SPACE, PLUGIN, object(), label="Mindful Path", timeout=30)
    assert not ok and "didn't finish" in why
    assert 1 < len(runs) < 20  # it waited between tries, never a hot loop
    assert space_vcs.binding_get(PLUGIN) is None  # the bar's progress row is gone

    def lands(*a: Any, **k: Any) -> dict[str, Any]:
        head["seq"] += 1  # each try lands one changeset: it keeps going to the head
        return {"state": "ok", "tree": 0}

    monkeypatch.setattr(vcs_engine, "run", lands)
    monkeypatch.setattr(space_vcs, "blocked", lambda *a, **k: [])
    assert vcs_engine.pull_copy(env.account, SPACE, PLUGIN, object(), label="Mindful Path", timeout=30) == (True, "")
