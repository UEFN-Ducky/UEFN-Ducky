"""Plugin data scopes on one PC, sealed per account, and shared spaces through a fake
provider (no service: a space's copy lives here exactly like Local's).

Scopes: two accounts never see each other's rows or folders; signed-out ``_local`` is
separate; the pick is per plugin; ``sensitive`` docs never land in a shared copy; a
copy of Local into a space is one way; paused = read-only; access lost locks the copy
and the provider may drop it; the first use of a space pulls before a plugin seeds
defaults; without a provider there is only Local.
§13: another account can't read the DB or files; the owner reads them again after
signing back in; old plaintext is sealed once; an offline restart works; no key
yet = read-only, never plaintext.
What needs the team-sync engine (sync rounds, erase of a declared folder, team copies at
the writer) is tested with the engine, in Ducky Account.
"""

from __future__ import annotations

import hashlib
import sys
import time
from typing import Any

import pytest

# Every seal/open is DPAPI (plan §13): these checks need Windows.
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import scopes, spaces
from backend.uefn_plugins.scopes import PluginData


class _Who:
    """Which account is signed in (read at call time)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from frontend import duckyos_account

        self.account = ""
        monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: self.account)

    def be(self, email: str) -> str:
        self.account = f"https://uefnducky.org|{email}" if email else ""
        return scopes.account_id()


@pytest.fixture
def who(monkeypatch: pytest.MonkeyPatch) -> _Who:
    return _Who(monkeypatch)


ADK_ONLINE = {"on": True}


def _fake_adk() -> bytes:
    """The server's account data key: stable per account, only while "online"."""
    from frontend import duckyos_account

    if not ADK_ONLINE["on"]:
        raise OSError("offline")
    return hashlib.sha256(b"adk/" + duckyos_account.account_key().encode()).digest()


@pytest.fixture(autouse=True)
def account_keys(monkeypatch: pytest.MonkeyPatch):
    from backend.uefn_plugins import data_crypto

    data_crypto.reset_for_tests()
    ADK_ONLINE["on"] = True
    monkeypatch.setattr(data_crypto, "_fetch_adk", _fake_adk)
    yield
    data_crypto.reset_for_tests()


class FakeSpaces(spaces.Provider):
    """A spaces provider with nothing behind it: it names spaces, says whether a first
    pull can happen (and what it brings), and drops a lost copy when told to."""

    kind_label = "space"
    paused_label = "Space paused"

    def __init__(self) -> None:
        self.spaces: dict[str, str] = {}  # id → label
        self.server: dict[tuple[str, str, str], Any] = {}  # (space, plugin, key) → doc a first pull brings
        self.online = True
        self.expired: set[str] = set()
        self.rounds: list[tuple[str, str]] = []

    def enabled(self) -> bool:
        return bool(self.spaces)

    def choices(self, purpose: str) -> list[dict[str, Any]]:
        return [{"id": sid, "label": label, "members": 3} for sid, label in self.spaces.items()]

    def choices_note(self, purpose: str) -> str:
        return "No spaces yet."

    def linked(self, account: str, plugin: str, space_id: str) -> None:
        repo.sync_put(account, space_id, label=self.spaces.get(space_id, space_id), members=3)

    def first_pull(self, account: str, space_id: str, timeout: float) -> bool:
        if not self.online:
            return False
        from backend.uefn_plugins.data_crypto import seal_text

        for (sid, plugin, key), value in self.server.items():
            if sid == space_id:
                data = scopes.encode_doc(value)
                repo.put(account, sid, plugin, "doc", key, value=seal_text(data.decode("utf-8"), account),
                         size=len(data), sha256=hashlib.sha256(data).hexdigest(), dirty=False, rev=1)
        repo.sync_put(account, space_id, synced_at=time.time())
        return True

    def settle_lost(self, account: str, space_id: str) -> str:
        if space_id in self.expired:
            scopes.purge_space(account, space_id)
            return "unavailable"
        return "lost"

    def sync(self, account: str, space_id: str, *, force: bool = False) -> dict[str, Any]:
        self.rounds.append((account, space_id))
        return {"state": "ok", "changed": [], "error": ""}


@pytest.fixture
def fake_spaces(account_keys) -> FakeSpaces:
    fake = FakeSpaces()
    spaces.install_for_tests(fake)
    yield fake
    spaces.reset_for_tests()


def open_space(fake: FakeSpaces, who: _Who, email: str, space: str, label: str, plugin: str = "brainrot-tcg") -> str:
    """Sign in as ``email`` and point ``plugin`` at ``space``, already pulled once."""
    aid = who.be(email)
    fake.spaces[space] = label
    scopes.link_plugin(plugin, space)
    repo.sync_put(aid, space, synced_at=time.time())
    return aid


# --------------------------------------------------------------------------- scopes on one PC


def test_accounts_plugins_and_local_never_share_rows_or_folders(who: _Who, fake_spaces: FakeSpaces) -> None:
    from frontend.ui_web import plugin_host_api as pha

    cards = PluginData("brainrot-tcg")
    scenes = PluginData("forge")
    ana = who.be("ana@x.org")
    cards.put("card.pip", {"name": "Pip"})
    cards.put_file("assets/pip.png", b"PNG-ana")
    pha.cache_set("brainrot-tcg", "ui", {"tab": "cards"})
    ana_dir = scopes.plugin_dir(scopes.active_scope("brainrot-tcg"), "brainrot-tcg")
    assert ana_dir.is_dir()

    bo = who.be("bo@x.org")
    assert bo != ana and cards.get("card.pip") is None and cards.keys() == [] and cards.files() == []
    assert pha.cache_get("brainrot-tcg", "ui") == {}
    bo_dir = scopes.plugin_dir(scopes.active_scope("brainrot-tcg"), "brainrot-tcg")
    assert bo_dir != ana_dir and ana not in str(bo_dir)

    local = who.be("")
    assert local == scopes.LOCAL and cards.get("card.pip") is None
    cards.put("card.pip", {"name": "Local Pip"})

    # The pick is per plugin: BrainrotTCG → space T shows the space's copy, Forge stays Local.
    who.be("ana@x.org")
    scenes.put("scene.a", {"by": "ana"})
    open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    scope = scopes.active_scope("brainrot-tcg")
    assert (scope["kind"], scope["label"], scope["spaceId"]) == ("shared", "Alpha Studio", "spaceT")
    assert cards.keys() == []
    assert scopes.active_scope("forge")["kind"] == "personal" and scenes.get("scene.a") == {"by": "ana"}
    cards.put("card.pip", {"name": "Shared Pip"})
    scopes.link_plugin("brainrot-tcg", scopes.PERSONAL)
    assert cards.get("card.pip") == {"name": "Pip"}  # nothing moved on either switch
    assert cards.get_file("assets/pip.png") == b"PNG-ana"
    scopes.link_plugin("brainrot-tcg", "spaceT")
    assert cards.get("card.pip") == {"name": "Shared Pip"}
    who.be("")
    assert cards.get("card.pip") == {"name": "Local Pip"}
    with pytest.raises(ValueError, match="Sign in"):
        scopes.link_plugin("brainrot-tcg", "spaceT")


def test_two_spaces_of_one_account_never_share_a_plugins_data(who: _Who, fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    open_space(fake_spaces, who, "ana@x.org", "spaceU", "Beta Crew", plugin="forge")
    cards.put("card.pip", {"space": "T"})
    # Switching BrainrotTCG to space U shows U's own (empty) copy: T's card never arrives there.
    scopes.link_plugin("brainrot-tcg", "spaceU")
    repo.sync_put(ana, "spaceU", synced_at=time.time())
    assert cards.keys() == []
    cards.put("card.mo", {"space": "U"})
    assert [r["key"] for r in repo.rows(ana, "spaceU", "brainrot-tcg", "doc")] == ["card.mo"]
    scopes.link_plugin("brainrot-tcg", "spaceT")
    assert cards.items() == {"card.pip": {"space": "T"}}
    assert {p: s for p, s in repo.links(ana).items() if p not in scopes.CORE_DATA} == {
        "brainrot-tcg": "spaceT", "forge": "spaceU"}


def test_workflows_and_general_storage_follow_the_team_and_never_leave_it_silently(
        who: _Who, fake_spaces: FakeSpaces) -> None:
    from backend.uefn_plugins.general_storage import GENERAL

    wf = spaces.WORKFLOW_DOCS
    assert scopes.CORE_DATA == (wf, GENERAL)
    # Nothing ever linked core data (0013 skipped ``ducky.*``; General storage starts Local), so
    # the Team modal hid it although the team copy held the workflows. The first plugin on a
    # team now takes them along.
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    assert repo.links(ana) == {"brainrot-tcg": "spaceT", wf: "spaceT", GENERAL: "spaceT"}
    # Core data on a team stays there when plugins move: never off a team silently.
    open_space(fake_spaces, who, "ana@x.org", "spaceU", "Beta Crew", plugin="forge")
    scopes.link_plugin("brainrot-tcg", "spaceU")
    assert (repo.link_get(ana, wf), repo.link_get(ana, GENERAL)) == ("spaceT", "spaceT")
    assert scopes.ensure_core_links() == []
    # No link (an older version's PC): they follow the team most plugins use.
    repo.link_set(ana, wf, scopes.PERSONAL)
    repo.link_set(ana, GENERAL, scopes.PERSONAL)
    assert scopes.ensure_core_links() == [wf, GENERAL]
    assert (repo.link_get(ana, wf), repo.link_get(ana, GENERAL)) == ("spaceU", "spaceU")
    # Every plugin Local, or signed out: nothing to follow.
    for pid in ("brainrot-tcg", "forge", wf, GENERAL):
        scopes.link_plugin(pid, scopes.PERSONAL)
    assert scopes.ensure_core_links() == [] and repo.links(ana) == {}
    who.be("")
    assert scopes.ensure_core_links() == []


def test_startup_puts_a_mixed_pc_on_one_copy_and_never_undoes_a_switch(who: _Who, fake_spaces: FakeSpaces,
                                                                      monkeypatch) -> None:
    from backend.uefn_plugins import store
    from backend.uefn_plugins.general_storage import GENERAL

    wf = spaces.WORKFLOW_DOCS
    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: ["brainrot-tcg", "forge", "kanban", "idle"])
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    scopes.link_plugin("forge", "spaceT")
    fake_spaces.spaces["spaceU"] = "Beta Crew"
    repo.link_set(ana, "kanban", "spaceU")  # an older version's per-plugin pick
    repo.link_set(ana, GENERAL, scopes.PERSONAL)
    # No app-wide copy yet: the team most plugins use wins, and everything goes there once.
    assert sorted(scopes.settle_app_copy()) == sorted(["kanban", "idle", GENERAL])
    assert scopes.app_copy() == "spaceT"
    assert {repo.link_get(ana, p) for p in ("brainrot-tcg", "forge", "kanban", "idle", wf, GENERAL)} == {"spaceT"}
    assert scopes.settle_app_copy() == []
    # After an app-wide switch to Local nothing goes back on the team at the next start.
    scopes.set_app_copy(scopes.PERSONAL)
    for pid in list(repo.links(ana)):
        scopes.link_plugin(pid, scopes.PERSONAL)
    assert scopes.settle_app_copy() == [] and repo.links(ana) == {}


def test_team_overview_always_lists_workflows_and_general_storage(who: _Who, fake_spaces: FakeSpaces) -> None:
    from backend.uefn_plugins.general_storage import GENERAL

    wf = spaces.WORKFLOW_DOCS
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    scopes.link_plugin(GENERAL, scopes.PERSONAL)
    PluginData(GENERAL).put("note.a", {"text": "kept Local"})
    over = spaces.team_overview()
    rows = {r["pluginId"]: r for r in over["plugins"]}
    assert over["current"] == "spaceT" and list(rows)[:2] == [wf, GENERAL]
    assert rows[wf]["linked"] is True
    assert (rows[GENERAL]["linked"], rows[GENERAL]["linkedTo"], rows[GENERAL]["localItems"]) == (False, "Local", 1)
    assert rows[GENERAL]["label"] == "General storage" and rows[GENERAL]["source"] == "storage"
    # "Use with team" is the plain link: then it is the team's row like any other.
    spaces.link_scope(GENERAL, "spaceT")
    assert {r["pluginId"]: r["linked"] for r in spaces.team_overview()["plugins"]}[GENERAL] is True
    assert repo.link_get(ana, GENERAL) == "spaceT"


def test_copy_local_into_a_space_is_one_way_and_keeps_the_spaces_items(who: _Who, fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = who.be("ana@x.org")
    cards.put("card.pip", {"v": "local"})
    cards.put("card.mo", {"v": "local"})
    cards.put("token", {"secret": 1}, sensitive=True)
    cards.put_file("assets/pip.png", b"PNG local")
    open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.pip", {"v": "shared"})
    status = spaces.scope_status("brainrot-tcg")
    assert status["local"] == {"docs": 2, "files": 1, "docsBytes": status["local"]["docsBytes"], "filesBytes": 9}
    assert status["current"]["docs"] == 1 and status["kindLabel"] == "space"

    assert scopes.copy_local_into("brainrot-tcg", "spaceT") == {"ok": True, "copied": 2, "kept": 1, "tooBig": []}
    assert cards.items() == {"card.mo": {"v": "local"}, "card.pip": {"v": "shared"}}
    assert cards.get_file("assets/pip.png") == b"PNG local"
    # Queued for the provider's next round; the sensitive doc stayed Local.
    dirty = {r["key"] for r in repo.rows(ana, "spaceT", "brainrot-tcg", "doc")} | {
        r["key"] for r in repo.rows(ana, "spaceT", "brainrot-tcg", "asset")}
    assert dirty == {"card.mo", "card.pip", "assets/pip.png"} and repo.count_dirty(ana, "spaceT") == 3
    # The Local copy is untouched.
    scopes.link_plugin("brainrot-tcg", scopes.PERSONAL)
    assert cards.items() == {"card.mo": {"v": "local"}, "card.pip": {"v": "local"}}


def test_sensitive_docs_stay_personal_and_never_land_in_a_shared_copy(who: _Who, fake_spaces: FakeSpaces,
                                                                      monkeypatch) -> None:
    from backend.agent import secrets as sec

    monkeypatch.setattr(sec, "protect_text", lambda s: b"blob-" + s.encode())
    monkeypatch.setattr(sec, "unprotect_text", lambda b: b[5:].decode())
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio", plugin="discord")
    data = PluginData("discord")
    data.put("token", {"bot": "very-secret"}, sensitive=True)
    data.put("channel", {"id": 7})
    assert data.get("token", sensitive=True) == {"bot": "very-secret"}
    assert "token" not in data.keys()
    assert [r["key"] for r in repo.rows(ana, "spaceT", "discord", "doc")] == ["channel"]


def test_a_pcs_own_items_stay_local_whatever_copy_the_plugin_shows(who: _Who, fake_spaces: FakeSpaces) -> None:
    """``"data_local": ["qa.", "device/"]``: what a panel notes about this PC (Island Media
    Kit wrote a diagnostics doc on every load) stays here. Before, each one went to the
    whole team, showed as someone editing it, and reset the open panels on every PC."""
    import json

    from backend.uefn_plugins.data_crypto import seal_text
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, plugin_dir

    pid = "island-media-kit"
    root = plugin_dir(pid)
    root.mkdir(parents=True)
    (root / PLUGIN_MANIFEST).write_text(json.dumps({"id": pid, "label": "Island Media Kit",
                                                    "data_local": ["qa.", "device/"]}))
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio", plugin=pid)
    data = PluginData(pid)
    data.put("project.a", {"name": "Island"})
    data.put("qa.boot.1", {"phase": "mounted"})
    data.put_file("device/log.txt", b"hello")
    data.put_file("art/logo.png", b"png")
    # One a teammate's older version of the plugin sent: in the team copy, never read here.
    raw = scopes.encode_doc({"phase": "theirs"})
    repo.put(ana, "spaceT", pid, "doc", "qa.boot.9", value=seal_text(raw.decode("utf-8"), ana), size=len(raw),
             sha256=hashlib.sha256(raw).hexdigest(), dirty=False, rev=3)

    assert [r["key"] for r in repo.rows(ana, "spaceT", pid, "doc")] == ["project.a", "qa.boot.9"]
    assert [r["key"] for r in repo.rows(ana, "spaceT", pid, "asset")] == ["art/logo.png"]
    assert [r["key"] for r in repo.rows(ana, scopes.PERSONAL, pid, "doc")] == ["qa.boot.1"]
    assert [r["key"] for r in repo.rows(ana, scopes.PERSONAL, pid, "asset")] == ["device/log.txt"]
    assert repo.count_dirty(ana, "spaceT", pid) == 2  # project.a and art/logo.png go out, nothing of qa.
    assert data.get("qa.boot.1") == {"phase": "mounted"} and data.get("qa.boot.9") is None
    assert data.keys() == ["project.a", "qa.boot.1"]
    assert data.keys("qa.") == data.keys("qa.boot.") == ["qa.boot.1"]
    assert data.keys("project.") == ["project.a"]
    assert data.items("q") == {"qa.boot.1": {"phase": "mounted"}}
    assert [f["path"] for f in data.files()] == ["art/logo.png", "device/log.txt"]
    assert data.get_file("device/log.txt") == b"hello" and data.has_file("device/log.txt")

    # A paused team copy is read-only; this PC's own items aren't the team's to pause.
    repo.sync_put(ana, "spaceT", state="paused")
    with pytest.raises(scopes.ReadOnlyScope):
        data.put("project.b", {"name": "B"})
    data.put("qa.boot.2", {"phase": "boot"})
    assert data.delete("qa.boot.1") is True and data.delete_file("device/log.txt") is True
    assert data.keys("qa.") == ["qa.boot.2"] and data.files("device/") == []
    repo.sync_put(ana, "spaceT", state="ok")

    # Copying Local into the team never takes them along.
    scopes.link_plugin(pid, scopes.PERSONAL)
    data.put("project.local", {"name": "Mine"})
    assert data.keys() == ["project.local", "qa.boot.2"]
    scopes.copy_local_into(pid, "spaceT")
    assert [r["key"] for r in repo.rows(ana, "spaceT", pid, "doc")] == ["project.a", "project.local", "qa.boot.9"]


def test_paused_is_read_only_and_access_lost_locks_then_the_provider_drops_it(who: _Who,
                                                                             fake_spaces: FakeSpaces) -> None:
    from backend.store.repos import plugin_kv
    from frontend.ui_web import plugin_host_api as host_api

    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    cards.put_file("assets/pip.png", b"pip")
    # Cache and prefs follow the shared copy too (kept on this PC, never synced).
    host_api.cache_set("brainrot-tcg", "deck", {"n": 1})
    host_api.prefs_plugin_set("brainrot-tcg", {"sort": "name"})
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope="spaceT") == ["deck"]
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope=scopes.PERSONAL) == []
    space_dir = scopes.scopes_root(ana) / "spaceT"
    assert space_dir.is_dir()

    repo.sync_put(ana, "spaceT", state="paused")
    assert scopes.active_scope("brainrot-tcg")["readOnly"] and cards.get("card.pip") == {"v": 1}
    with pytest.raises(scopes.ReadOnlyScope, match="paused"):
        cards.put("card.pip", {"v": 2})
    repo.sync_put(ana, "spaceT", state="ok")
    assert not scopes.active_scope("brainrot-tcg")["readOnly"]

    # Access lost: the copy here locks (unreadable, read-only), kept as it is.
    repo.sync_put(ana, "spaceT", state="lost")
    scope = scopes.active_scope("brainrot-tcg")
    assert (scope["kind"], scope["state"], scope["readOnly"]) == ("shared", "lost", True)
    assert cards.get("card.pip") is None and cards.keys() == [] and cards.get_file("assets/pip.png") is None
    with pytest.raises(scopes.ReadOnlyScope, match="no longer have access"):
        cards.put("card.pip", {"v": 3})
    assert host_api.cache_get("brainrot-tcg", "deck") == {} and host_api.prefs_plugin_get("brainrot-tcg") == {}
    with pytest.raises(scopes.ReadOnlyScope):
        host_api.cache_set("brainrot-tcg", "deck", {"n": 2})
    assert space_dir.is_dir() and repo.rows(ana, "spaceT", "brainrot-tcg", "doc")

    # Access back: the data unlocks as it was.
    repo.sync_put(ana, "spaceT", state="ok")
    assert cards.get("card.pip") == {"v": 1}
    assert host_api.cache_get("brainrot-tcg", "deck") == {"n": 1}
    assert host_api.prefs_plugin_get("brainrot-tcg") == {"sort": "name"}

    # Lost again, and the provider decides the copy here goes (the server's is not core's business).
    repo.sync_put(ana, "spaceT", state="lost")
    fake_spaces.expired.add("spaceT")
    assert scopes.active_scope("brainrot-tcg")["kind"] == "personal"  # the plugin's link went with it
    assert not space_dir.exists() and repo.rows(ana, "spaceT", "brainrot-tcg", "doc") == []
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope="spaceT") == []
    assert plugin_kv.all_prefs(account=ana, scope="spaceT") == {}
    assert repo.links(ana) == {}


def test_a_plugin_test_runs_in_a_sandbox_that_never_touches_the_users_data(who: _Who) -> None:
    from frontend.ui_web import plugin_host_api as host_api

    import threading

    who.be("ana@x.org")
    cards = PluginData("brainrot-tcg")
    other = PluginData("other-plugin")
    cards.put("card.pip", {"v": 1})
    cards.put("token", {"t": "real"}, sensitive=True)
    host_api.cache_set("brainrot-tcg", "deck", {"n": 1})
    with scopes.sandbox("brainrot-tcg") as box:
        assert box["id"].startswith(scopes.SANDBOX_PREFIX)
        assert scopes.active_scope("brainrot-tcg")["id"] == box["id"]
        assert cards.get("card.pip") is None and host_api.cache_get("brainrot-tcg", "deck") == {}
        assert cards.get("token", sensitive=True) is None
        cards.put("card.junk", {"v": 0})
        cards.put("token", {"t": "junk"}, sensitive=True)
        host_api.cache_set("brainrot-tcg", "deck", {"n": 0})
        # A thread the plugin starts itself lands in the sandbox too; other plugins don't.
        worker = threading.Thread(target=lambda: cards.put("card.thread", {"v": 0}))
        worker.start()
        worker.join()
        other.put("note", {"v": 1})
        assert scopes.active_scope("other-plugin")["id"] == scopes.PERSONAL
        with pytest.raises(ValueError, match="already running"):
            scopes.sandbox_begin("brainrot-tcg")
    assert cards.keys() == ["card.pip"]  # sensitive docs are never listed
    assert cards.get("card.pip") == {"v": 1} and cards.get("card.thread") is None
    assert cards.get("token", sensitive=True) == {"t": "real"}
    assert host_api.cache_get("brainrot-tcg", "deck") == {"n": 1} and other.get("note") == {"v": 1}
    assert repo.rows(scopes.account_id(), box["id"], "brainrot-tcg", "doc") == []


def test_without_a_provider_there_is_only_local(who: _Who) -> None:
    ana = who.be("ana@x.org")
    assert not spaces.has_provider()
    assert [c["id"] for c in spaces.scope_choices()["choices"]] == ["personal"]
    assert spaces.scope_status("brainrot-tcg")["visible"] is False  # no bar, no teaser
    # A copy linked while a provider was there stays here and takes writes; they queue,
    # and the bar and Sync now say nothing can sync.
    repo.link_set(ana, "brainrot-tcg", "spaceT")
    repo.sync_put(ana, "spaceT", label="Alpha Studio", synced_at=time.time())
    scope = scopes.active_scope("brainrot-tcg")
    assert (scope["kind"], scope["state"], scope["readOnly"]) == ("shared", "no_provider", False)
    PluginData("brainrot-tcg").put("card.pip", {"v": 1})
    assert repo.count_dirty(ana, "spaceT") == 1
    status = spaces.scope_status("brainrot-tcg")
    assert (status["visible"], status["state"], status["error"], status["pending"]) == (
        True, "no_provider", "Not connected: Ducky Account isn't running", 1)
    assert spaces.sync_active("brainrot-tcg", force=True) == {
        "ok": False, "started": False, "error": "Not connected: Ducky Account isn't running"}
    assert spaces.sync_active("forge") == {"ok": True, "started": False}  # Local needs no provider


def test_a_plugin_that_never_shares_its_data_is_always_local(who: _Who, fake_spaces: FakeSpaces) -> None:
    import json

    from backend.uefn_plugins.store import PLUGIN_MANIFEST, plugin_dir

    root = plugin_dir("browser")
    root.mkdir(parents=True)
    (root / PLUGIN_MANIFEST).write_text(json.dumps({"id": "browser", "label": "Browser", "data_sharing": False}))
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    assert spaces.scope_status("brainrot-tcg")["visible"] is True
    # No bar, no Data picker, no way into a space — even a link made before the flag.
    assert spaces.scope_status("browser")["visible"] is False
    with pytest.raises(ValueError, match="Browser keeps its data on this PC only"):
        scopes.link_plugin("browser", "spaceT")
    with pytest.raises(ValueError, match="on this PC only"):
        scopes.copy_local_into("browser", "spaceT")
    repo.link_set(ana, "browser", "spaceT")
    PluginData("browser").put("tab.home", {"url": "https://example.org"})
    assert scopes.active_scope("browser")["kind"] == "personal"
    assert repo.rows(ana, "spaceT", "browser", "doc") == []
    assert scopes.link_plugin("browser", scopes.PERSONAL)["kind"] == "personal"


def test_signing_in_or_out_restarts_and_reloads_plugins_that_show_a_space(who: _Who, fake_spaces: FakeSpaces,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from backend.uefn_plugins import host, store
    from frontend.ui_web.agent_turns.testing import patch_turns

    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio", plugin="forge")
    restarted: list[str] = []
    pushed: list[dict[str, Any]] = []
    done = threading.Event()
    monkeypatch.setattr(store, "get_enabled_plugin_ids", lambda: ["brainrot-tcg", "tester"])
    monkeypatch.setattr(host, "reload_single_plugin", restarted.append)
    patch_turns(monkeypatch, "push_ui_event", lambda e: (pushed.append(e), done.set()))

    # Signing out: both plugins that showed space T move to Local; only the enabled one restarts.
    spaces.on_account_change(ana, scopes.LOCAL)
    assert done.wait(5)
    assert restarted == ["brainrot-tcg"]
    assert pushed == [{"type": "plugin_scope_changed", "plugins": ["brainrot-tcg", *scopes.CORE_DATA, "forge"],
                       "switched": True}]

    # An account with every plugin on Local has nothing to restart.
    done.clear()
    spaces.on_account_change(scopes.LOCAL, who.be("bo@x.org"))
    assert not done.wait(0.2) and restarted == ["brainrot-tcg"]


def test_the_picker_lists_the_providers_spaces_and_a_fresh_link_waits(who: _Who, fake_spaces: FakeSpaces) -> None:
    who.be("ana@x.org")
    out = spaces.scope_choices()
    assert out["choices"] == [{"id": "personal", "kind": "personal", "label": "Local"}]
    assert out["emptyNote"] == "No spaces yet." and out["kindLabel"] == "space"
    assert spaces.scope_status("brainrot-tcg")["visible"] is False
    fake_spaces.spaces["spaceT"] = "Alpha Studio"
    choices = spaces.scope_choices()["choices"]
    assert [(c["id"], c["kind"], c.get("members")) for c in choices] == [
        ("personal", "personal", None), ("spaceT", "shared", 3)]
    fake_spaces.online = False
    status = spaces.link_scope("brainrot-tcg", "spaceT")
    # Fresh link: read-only "waiting" until the first pull lands.
    assert status["scope"] == {"kind": "shared", "label": "Alpha Studio", "spaceId": "spaceT", "readOnly": True}
    assert status["visible"] and status["state"] == "waiting" and status["members"] == 3 and status["pending"] == 0


def test_first_use_of_a_space_pulls_before_a_plugin_can_seed_defaults(who: _Who, fake_spaces: FakeSpaces) -> None:
    def load_or_seed(data: PluginData) -> dict[str, Any]:
        """What BrainrotTCG's load_db does: an empty scope gets the bundled defaults."""
        docs = data.items()
        if not docs:
            data.put("meta", {"seeded": True})
            data.put("card.default", {"name": "Seed"})
            return data.items()
        return docs

    cards = PluginData("brainrot-tcg")
    fake_spaces.spaces["spaceT"] = "Alpha Studio"
    fake_spaces.server[("spaceT", "brainrot-tcg", "card.pip")] = {"name": "Pip"}
    fake_spaces.server[("spaceT", "brainrot-tcg", "meta")] = {"space": True}

    # Bo links the space for the first time: the first pull lands before the plugin reads.
    who.be("bo@x.org")
    scopes.link_plugin("brainrot-tcg", "spaceT")
    assert load_or_seed(cards) == {"card.pip": {"name": "Pip"}, "meta": {"space": True}}

    # Cy's first use is offline: read-only "waiting", the seed is refused, nothing is written.
    cy = who.be("cy@x.org")
    fake_spaces.online = False
    scopes.link_plugin("brainrot-tcg", "spaceT")
    with pytest.raises(scopes.ReadOnlyScope, match="Waiting for Alpha Studio's data"):
        load_or_seed(cards)
    assert scopes.active_scope("brainrot-tcg")["state"] == "waiting"
    assert repo.rows(cy, "spaceT", "brainrot-tcg", "doc") == []
    # Back online, the next read pulls and the scope opens.
    fake_spaces.online = True
    assert load_or_seed(cards)["card.pip"] == {"name": "Pip"}
    assert not scopes.active_scope("brainrot-tcg")["readOnly"]
    assert repo.get(cy, "spaceT", "brainrot-tcg", "doc", "card.default") is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_a_bridge_process_follows_an_account_switch_made_in_the_app(monkeypatch) -> None:
    """The MCP bridge caches secrets per process: after the app signs in as another
    account, the bridge's next tool write lands in the new account's scope."""
    import json

    from backend.agent import secrets as sec
    from backend.store.repos import secrets as secrets_repo

    login = lambda email: json.dumps({"base_url": "https://uefnducky.org", "email": email, "device_key": "dky_v1_x"})  # noqa: E731
    sec.set_key("duckyos_account", login("ana@x.org"))
    cards = PluginData("brainrot-tcg")
    cards.put("card.a", {"by": "ana"})
    ana = scopes.account_id()
    # The app (another process) signs in as Bo: only the stored row changes; this process's cache still says Ana.
    secrets_repo.set_blob("duckyos_account", sec.protect_text(login("bo@x.org")))
    assert "ana@x.org" in (sec.get_key("duckyos_account") or "")
    cards.put("card.b", {"by": "bo"})
    bo = scopes.account_id()
    assert bo != ana
    assert repo.get(bo, "personal", "brainrot-tcg", "doc", "card.b") is not None
    assert repo.get(ana, "personal", "brainrot-tcg", "doc", "card.b") is None


def _in_other_connection(fn) -> None:
    """Run *fn* on a new thread: its own ducky.db connection, like another process."""
    import threading

    t = threading.Thread(target=fn)
    t.start()
    t.join()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")
def test_the_account_is_read_once_until_the_login_row_changes(monkeypatch) -> None:
    import json

    from backend.agent import secrets as sec
    from backend.store.repos import secrets as secrets_repo
    from frontend import duckyos_account

    login = lambda email: json.dumps({"base_url": "https://uefnducky.org", "email": email, "device_key": "dky_v1_x"})  # noqa: E731
    sec.set_key("duckyos_account", login("ana@x.org"))
    duckyos_account.forget_account_key()
    reads = {"n": 0}
    real_get = secrets_repo.get_blob

    def counted(name: str):
        reads["n"] += 1
        return real_get(name)

    monkeypatch.setattr(secrets_repo, "get_blob", counted)
    ana = scopes.account_id()
    for _ in range(999):
        assert scopes.account_id() == ana
    assert reads["n"] == 1

    # Another connection commits something else: still cached, no secrets read.
    _in_other_connection(lambda: secrets_repo.set_blob("openai", sec.protect_text("sk-x")))
    assert scopes.account_id() == ana
    assert reads["n"] == 1

    # Another connection signs in as Bo: the next call follows.
    _in_other_connection(lambda: secrets_repo.set_blob("duckyos_account", sec.protect_text(login("bo@x.org"))))
    bo = scopes.account_id()
    assert bo == scopes.account_id_for("https://uefnducky.org|bo@x.org") and bo != ana

    # The login is deleted elsewhere: signed out.
    _in_other_connection(lambda: secrets_repo.delete("duckyos_account"))
    assert scopes.account_id() == scopes.LOCAL


# --------------------------------------------------------------------------- sync bookkeeping


def _sha_of(account: str, key: str, space: str = "spaceT", kind: str = "doc") -> str:
    return str(repo.get(account, space, "brainrot-tcg", kind, key)["sha256"])


def _queue(account: str, limit: int = 100) -> list[str]:
    return [r["key"] for r in repo.dirty(account, "spaceT", limit)]


def test_a_content_push_confirmed_after_a_local_delete_keeps_the_delete(who: _Who, fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.pip", rev=5, sha256=_sha_of(ana, "card.pip"))
    assert _queue(ana) == []
    cards.put("card.pip", {"v": 2})
    pushed = repo.dirty(ana, "spaceT", 100)[0]  # a round reads the row and pushes it on base 5
    cards.delete("card.pip")  # deleted while that push is in flight
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.pip", rev=6, sha256=pushed["sha256"])
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.pip")
    # Before, the tombstone went here: the delete never pushed and the doc came back everywhere.
    assert (row["deleted"], row["dirty"], row["rev"], row["sha256"]) == (1, 1, 6, pushed["sha256"])
    assert _queue(ana) == ["card.pip"]
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.pip", rev=7, sha256="", pushed_delete=True)
    assert repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.pip") is None

    # An edit made while a content push is in flight stays queued on the new base rev.
    cards.put("card.mo", {"v": 1})
    sent = _sha_of(ana, "card.mo")
    cards.put("card.mo", {"v": 2})
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.mo", rev=8, sha256=sent)
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.mo")
    assert (row["dirty"], row["rev"]) == (1, 8)
    # A delete confirmed after the key was made again keeps the new copy, on the delete's rev.
    cards.delete("card.mo")
    cards.put("card.mo", {"v": 3})
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.mo", rev=9, sha256="")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.mo")
    assert (row["deleted"], row["dirty"], row["rev"]) == (0, 1, 9) and cards.get("card.mo") == {"v": 3}


def test_a_rev_0_delete_while_its_push_is_in_flight_leaves_a_tombstone_that_pushes(who: _Who,
                                                                                   fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.new", {"v": 1})  # never pushed: rev 0
    pushed = repo.dirty(ana, "spaceT", 100)[0]
    cards.delete("card.new")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.new")
    assert (row["deleted"], row["dirty"], row["rev"]) == (1, 1, 0)  # before: the row just went
    # The commit lands.
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.new", rev=3, sha256=pushed["sha256"])
    assert [(r["key"], r["deleted"], r["rev"]) for r in repo.dirty(ana, "spaceT", 100)] == [("card.new", 1, 3)]
    # An older provider's answer to the delete (no pushed_delete flag, an empty sha).
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.new", rev=4, sha256="")
    assert repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.new") is None
    # Files too; Local deletes still just go.
    cards.put_file("assets/new.png", b"png")
    assert cards.delete_file("assets/new.png")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/new.png")
    assert (row["deleted"], row["dirty"]) == (1, 1) and not cards.has_file("assets/new.png")
    scopes.link_plugin("brainrot-tcg", scopes.PERSONAL)
    cards.put("card.local", {"v": 1})
    assert cards.delete("card.local") and repo.get(ana, scopes.PERSONAL, "brainrot-tcg", "doc", "card.local") is None


def test_a_file_delete_drops_the_row_and_the_file_together(who: _Who, fake_spaces: FakeSpaces,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put_file("assets/pip.png", b"png")
    path = scopes.asset_file(scopes.active_scope("brainrot-tcg"), "brainrot-tcg", "assets/pip.png")
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        if self == path:
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    # The file can't go (held open): the row stays live with its file, never a row without one.
    monkeypatch.setattr(Path, "unlink", held_open)
    with pytest.raises(PermissionError):
        cards.delete_file("assets/pip.png")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png")
    assert row["deleted"] == 0 and cards.has_file("assets/pip.png")
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert cards.delete_file("assets/pip.png")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png")
    assert (row["deleted"], row["dirty"]) == (1, 1) and not path.exists()


def test_a_pull_never_overwrites_a_local_write_made_during_its_download(who: _Who, fake_spaces: FakeSpaces) -> None:
    import threading

    from backend.store import db
    from backend.uefn_plugins.data_crypto import seal_bytes, seal_text

    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "doc", "card.pip", rev=1, sha256=_sha_of(ana, "card.pip"))
    expect = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.pip")

    def in_another_connection(fn: Any) -> None:
        def run() -> None:
            try:
                fn()
            finally:
                db.close_thread_connections()

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(10)

    # The download runs; meanwhile the MCP bridge (another connection) saves the doc.
    in_another_connection(lambda: cards.put("card.pip", {"v": "mine"}))
    theirs = scopes.encode_doc({"v": "theirs"})
    remote = {"value": seal_text(theirs.decode("utf-8"), ana), "size": len(theirs),
              "sha256": hashlib.sha256(theirs).hexdigest(), "rev": 2}
    assert repo.apply_remote(ana, "spaceT", "brainrot-tcg", "doc", "card.pip", expect=expect, **remote) is False
    assert cards.get("card.pip") == {"v": "mine"}
    assert repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.pip")["dirty"]
    # Nothing written meanwhile: the pulled copy lands clean.
    assert repo.apply_remote(ana, "spaceT", "brainrot-tcg", "doc", "card.new", expect=None, **remote) is True
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.new")
    assert cards.get("card.new") == {"v": "theirs"} and (row["dirty"], row["rev"]) == (0, 2)

    # A file: the temp next to the target replaces it in the same transaction, or goes.
    scope = scopes.active_scope("brainrot-tcg")
    target = scopes.asset_file(scope, "brainrot-tcg", "assets/pip.png")
    target.parent.mkdir(parents=True, exist_ok=True)

    def temp_with(data: bytes) -> Any:
        temp = target.with_name(f"pip.{len(data)}.tmp")
        temp.write_bytes(seal_bytes(data, ana))
        return temp

    file_remote = {"value": None, "size": 6, "sha256": hashlib.sha256(b"theirs").hexdigest(), "rev": 3}
    temp = temp_with(b"theirs")
    assert repo.apply_remote(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png", expect=None, temp_path=temp,
                             target_path=target, **file_remote) is True
    assert cards.get_file("assets/pip.png") == b"theirs" and not temp.exists()
    expect = repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png")
    in_another_connection(lambda: cards.put_file("assets/pip.png", b"my edit"))
    temp = temp_with(b"newer")
    assert repo.apply_remote(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png", expect=expect, temp_path=temp,
                             target_path=target, **{**file_remote, "rev": 4}) is False
    assert cards.get_file("assets/pip.png") == b"my edit" and not temp.exists()
    # A pulled delete waits for the same: a local edit keeps the row and the file.
    assert repo.remove_remote(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png", expect=expect,
                              target_path=target) is False
    assert cards.get_file("assets/pip.png") == b"my edit"
    repo.mark_pushed(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png", rev=5,
                     sha256=_sha_of(ana, "assets/pip.png", kind="asset"))
    expect = repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png")
    assert repo.remove_remote(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png", expect=expect,
                              target_path=target) is True
    assert repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/pip.png") is None and not target.exists()
    assert list(target.parent.glob("*.tmp")) == []


def test_a_prefix_is_exact(who: _Who) -> None:
    from backend.store.repos import plugin_kv

    who.be("ana@x.org")
    data = PluginData("scene-maker")
    for key in ("scene_1_mesh", "scene_10_x", "scene-1-x", "scene_1"):
        data.put(key, {"k": key})
    data.put_file("scene_1_/a.bin", b"a")
    data.put_file("scene_10/b.bin", b"b")
    assert data.items("scene_1_") == {"scene_1_mesh": {"k": "scene_1_mesh"}}
    assert data.keys("scene_1_") == ["scene_1_mesh"]
    assert data.keys("scene_1") == ["scene_1", "scene_10_x", "scene_1_mesh"]
    assert [f["path"] for f in data.files("scene_1_")] == ["scene_1_/a.bin"]
    assert len(data.keys()) == 4

    # Cache prefixes too: clearing "a_b" leaves "aXb1" and "a%b".
    for key in ("a_bc", "aXb1", "a_b", "a%b"):
        plugin_kv.set("scene-maker", key, 1, account="acc", scope=scopes.PERSONAL)
    assert plugin_kv.delete_prefix("scene-maker", "a_b", account="acc", scope=scopes.PERSONAL) == ["a_b", "a_bc"]
    assert plugin_kv.keys("scene-maker", account="acc", scope=scopes.PERSONAL) == ["a%b", "aXb1"]


def test_a_row_that_keeps_failing_backs_off_and_never_blocks_newer_rows(who: _Who, fake_spaces: FakeSpaces,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    clock = [time.time()]
    monkeypatch.setattr(repo, "time", types.SimpleNamespace(time=lambda: clock[0]))
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put_file("assets/gone.png", b"x")  # the oldest change: its file goes missing
    clock[0] += 1
    cards.put("card.new", {"v": 1})
    assert _queue(ana, 1) == ["assets/gone.png"]

    waits = []
    for n in range(1, repo.BLOCK_AFTER + 1):
        out = repo.note_attempt(ana, "spaceT", "brainrot-tcg", "asset", "assets/gone.png", "File missing")
        waits.append(round(out["next_try"] - clock[0]))
        assert out["attempts"] == n
        assert _queue(ana, 1) == ["card.new"]  # backing off: newer rows go first
        clock[0] = out["next_try"]
        if n < repo.BLOCK_AFTER:
            assert _queue(ana, 1) == ["card.new"]  # due again, but fewest attempts first
            assert _queue(ana) == ["card.new", "assets/gone.png"]
    assert waits == [60, 120, 240, 480, 960] and out["state"] == "blocked"
    clock[0] += 100_000
    assert _queue(ana) == ["card.new"] and repo.count_dirty(ana, "spaceT") == 1
    status = spaces.scope_status("brainrot-tcg")
    assert (status["pending"], status["blocked"], status["refused"]) == (1, 1, 0)
    assert status["items"] == [{"kind": "asset", "key": "assets/gone.png", "state": "blocked", "reason": "File missing",
                                "attempts": 5, "deleted": False}]
    assert repo.note_attempt(ana, "spaceT", "brainrot-tcg", "doc", "card.none", "x") is None
    # Retry puts it back with fresh attempts.
    assert repo.set_state(ana, "spaceT", "brainrot-tcg", "asset", "assets/gone.png", "")
    assert _queue(ana) == ["assets/gone.png", "card.new"]
    assert repo.get(ana, "spaceT", "brainrot-tcg", "asset", "assets/gone.png")["attempts"] == 0


def test_a_refused_change_stays_listed_until_it_is_queued_again(who: _Who, fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.big", {"v": 1})
    cards.put("card.ok", {"v": 1})
    # An older provider refuses through clear_dirty: kept as refused, never "synced".
    repo.clear_dirty(ana, "spaceT", "brainrot-tcg", "doc", "card.big")
    row = repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.big")
    assert (row["dirty"], row["sync_state"], row["sync_error"]) == (1, "refused", repo.REFUSED_REASON)
    assert _queue(ana) == ["card.ok"] and repo.count_dirty(ana, "spaceT") == 1
    status = spaces.scope_status("brainrot-tcg")
    assert (status["pending"], status["refused"], status["blocked"], status["conflict"]) == (1, 1, 0, 0)
    assert [(i["key"], i["state"], i["reason"]) for i in status["items"]] == [
        ("card.big", "refused", repo.REFUSED_REASON)]
    # A refusal of content that was edited since never marks the new edit.
    sent = _sha_of(ana, "card.ok")
    cards.put("card.ok", {"v": 2})
    assert not repo.set_state(ana, "spaceT", "brainrot-tcg", "doc", "card.ok", "refused", "Storage is full",
                              expect_sha=sent)
    assert repo.set_state(ana, "spaceT", "brainrot-tcg", "doc", "card.ok", "refused", "Storage is full",
                          expect_sha=_sha_of(ana, "card.ok"))
    assert repo.state_counts(ana, "spaceT", "brainrot-tcg")["refused"] == 2 and _queue(ana) == []
    # The plan changed: both go back in the queue. A new edit of a refused row starts afresh too.
    assert repo.requeue_refused(ana, "spaceT") == 2 and _queue(ana) == ["card.big", "card.ok"]
    repo.set_state(ana, "spaceT", "brainrot-tcg", "doc", "card.big", "refused", "Too big")
    cards.put("card.big", {"v": 2})
    assert repo.get(ana, "spaceT", "brainrot-tcg", "doc", "card.big")["sync_state"] == ""
    with pytest.raises(ValueError, match="unknown sync state"):
        repo.set_state(ana, "spaceT", "brainrot-tcg", "doc", "card.big", "stuck")


def test_concurrent_sync_row_writes_keep_each_others_fields() -> None:
    import threading

    from backend.store import db

    start = threading.Barrier(2)

    def writer(fields: Any) -> None:
        try:
            start.wait(5)
            for n in range(40):
                repo.sync_put("acc", "spaceT", **fields(n))
        finally:
            db.close_thread_connections()

    workers = [
        threading.Thread(target=writer, args=(lambda n: {"label": f"Alpha {n}", "members": n},)),  # a picker refresh
        threading.Thread(target=writer, args=(lambda n: {"cursor_rev": n, "synced_at": float(n)},)),  # a round
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    row = repo.sync_get("acc", "spaceT")
    assert (row["label"], row["members"], row["cursor_rev"], row["synced_at"], row["state"]) == (
        "Alpha 39", 39, 39, 39.0, "ok")
    repo.sync_put("acc", "spaceT", nothing="ignored", usage={"usedBytes": 5})
    assert repo.sync_get("acc", "spaceT")["usage"] == {"usedBytes": 5}


def test_an_unavailable_space_stays_read_only_with_its_reason_and_never_writes_local(who: _Who,
                                                                                      fake_spaces: FakeSpaces) -> None:
    cards = PluginData("brainrot-tcg")
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    repo.sync_put(ana, "spaceT", state="unavailable", error="permission denied: teams")
    scope = scopes.active_scope("brainrot-tcg")
    assert (scope["kind"], scope["state"], scope["readOnly"]) == ("shared", "unavailable", True)
    assert cards.get("card.pip") == {"v": 1}
    with pytest.raises(scopes.ReadOnlyScope,
                       match=r"Alpha Studio isn't available right now \(permission denied: teams\)\. Read-only"):
        cards.put("card.mo", {"v": 1})
    with pytest.raises(scopes.ReadOnlyScope):
        cards.delete("card.pip")
    assert repo.rows(ana, scopes.PERSONAL, "brainrot-tcg", "doc") == []
    status = spaces.scope_status("brainrot-tcg")
    assert (status["state"], status["error"], status["scope"]["readOnly"]) == (
        "unavailable", "permission denied: teams", True)
    # A plugin with no copy left in the space (the provider dropped it) shows Local.
    repo.link_set(ana, "forge", "spaceT")
    assert scopes.active_scope("forge")["kind"] == "personal"
    # Reset on the server: read-only too, until the user picks what to keep.
    repo.sync_put(ana, "spaceT", state="reset", error="")
    assert scopes.active_scope("brainrot-tcg")["readOnly"]
    with pytest.raises(scopes.ReadOnlyScope, match="reset on the server"):
        cards.put("card.pip", {"v": 2})
    assert scopes.space_access("spaceT")[0] is True  # membership didn't change


def test_only_plugins_on_the_host_data_service_are_offered_a_space(who: _Who, fake_spaces: FakeSpaces,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from backend.uefn_plugins import host
    from backend.uefn_plugins.store import PLUGIN_MANIFEST, plugin_dir

    def manifest(pid: str, **fields: Any) -> None:
        root = plugin_dir(pid)
        root.mkdir(parents=True)
        (root / PLUGIN_MANIFEST).write_text(json.dumps({"id": pid, "label": pid, **fields}))

    who.be("ana@x.org")
    fake_spaces.spaces["spaceT"] = "Alpha Studio"
    # Keeps its own files (a Forge-like plugin): nothing to share, so no bar at all.
    assert scopes.shareable("scene-maker") is False
    status = spaces.scope_status("scene-maker")
    assert (status["visible"], status["shareable"]) == (False, False)
    PluginData("scene-maker").put("scene.a", {"v": 1})
    assert scopes.shareable("scene-maker")
    status = spaces.scope_status("scene-maker")
    assert (status["visible"], status["shareable"]) == (True, True)
    assert scopes.shareable("ducky.automations")
    manifest("declares", data_sharing=True)
    assert scopes.shareable("declares")
    monkeypatch.setattr(host, "_DATA_PLUGINS", {"api-user"})
    assert scopes.shareable("api-user")
    manifest("never", data_sharing=False)
    PluginData("never").put("tab.home", {"v": 1})
    assert not scopes.shareable("never") and spaces.scope_status("never")["visible"] is False
    fake_spaces.spaces.clear()  # no spaces to offer: no bar at all
    assert spaces.scope_status("scene-maker")["visible"] is False


def test_shared_copies_refuse_what_wont_fit_once_sealed(who: _Who, fake_spaces: FakeSpaces,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cards = PluginData("brainrot-tcg")
    near = {"x": "a" * (scopes.DOC_MAX_BYTES - 8 - 10)}  # 10 bytes under 1 MB once encoded
    assert len(scopes.encode_doc(near)) == scopes.DOC_MAX_BYTES - 10
    monkeypatch.setattr(scopes, "ASSET_MAX_BYTES", 1000)
    who.be("ana@x.org")
    assert cards.put("card.big", near)["changed"]  # Local keeps the full limits
    assert cards.put_file("assets/big.bin", b"x" * 990)["changed"]
    ana = open_space(fake_spaces, who, "ana@x.org", "spaceT", "Alpha Studio")
    with pytest.raises(ValueError, match="too big to share"):
        cards.put("card.big", near)
    with pytest.raises(ValueError, match=r"too big to share \(over 967 bytes\)"):
        cards.put_file("assets/big.bin", b"x" * 990)
    assert cards.put_file("assets/ok.bin", b"x" * 967)["changed"]
    assert repo.rows(ana, "spaceT", "brainrot-tcg", "doc") == []
    # A copy of Local into the space leaves them Local instead of queueing what the server refuses.
    assert scopes.copy_local_into("brainrot-tcg", "spaceT") == {
        "ok": True, "copied": 0, "kept": 0, "tooBig": ["card.big", "assets/big.bin"]}
    assert repo.count_dirty(ana, "spaceT") == 1  # assets/ok.bin only


# --------------------------------------------------------------------------- §13 sealed per account


def _raw_doc(account: str, key: str) -> str:
    row = repo.get(account, "personal", "brainrot-tcg", "doc", key)
    return str(row["value"]) if row else ""


def test_another_account_cant_read_the_db_or_files_and_the_owner_can_again(who: _Who) -> None:
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import data_crypto
    from frontend.ui_web import plugin_host_api as pha

    cards = PluginData("brainrot-tcg")
    ana = who.be("ana@x.org")
    cards.put("card.pip", {"name": "Pip the secret"})
    cards.put_file("assets/pip.png", b"PNG pip secret")
    pha.cache_set("brainrot-tcg", "ui", {"tab": "secret-tab"})

    # On disk: ciphertext only, in the DB and in the file.
    doc = _raw_doc(ana, "card.pip")
    kv_value, _ = plugin_kv.get("brainrot-tcg", "ui", account=ana, scope="personal")
    file_bytes = scopes.asset_file(scopes.personal_scope(ana), "brainrot-tcg", "assets/pip.png").read_bytes()
    assert doc.startswith(data_crypto.PREFIX) and "secret" not in doc
    assert str(kv_value).startswith(data_crypto.PREFIX) and "secret" not in str(kv_value)
    assert file_bytes.startswith(data_crypto.FILE_MAGIC) and b"secret" not in file_bytes

    # Bo signs in on the same Windows user: his key doesn't open Ana's rows, and hers isn't here.
    bo = who.be("bo@x.org")
    with pytest.raises(OSError):
        data_crypto._open(doc, b"adk:" + _fake_adk())
    with pytest.raises(data_crypto.Locked):
        data_crypto.open_text(doc, ana)
    with pytest.raises(data_crypto.Locked):
        data_crypto.open_bytes(file_bytes, ana)
    assert bo != ana and cards.get("card.pip") is None

    # Ana back in: everything opens.
    who.be("ana@x.org")
    assert cards.get("card.pip") == {"name": "Pip the secret"}
    assert cards.get_file("assets/pip.png") == b"PNG pip secret"
    assert pha.cache_get("brainrot-tcg", "ui") == {"tab": "secret-tab"}


def test_old_plaintext_rows_are_sealed_once_and_verified(who: _Who, monkeypatch) -> None:
    import base64
    import json

    from backend.agent.secrets import protect_text
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import data_crypto
    from frontend.ui_web import plugin_host_api as pha

    # Rows as the app wrote them before §13: plain JSON, and a sensitive DPAPI-only row.
    plugin_kv.set("translation", "es", {"Hello": "Hola"}, account=plugin_kv.UNCLAIMED, scope="personal")
    legacy = base64.b64encode(protect_text(json.dumps({"bot_token": "tok"}))).decode("ascii")
    plugin_kv.set("discord", "token", None, encrypted_b64=legacy, account=plugin_kv.UNCLAIMED, scope="personal")

    ana = who.be("ana@x.org")
    assert pha.cache_get("translation", "es") == {"Hello": "Hola"}
    assert pha.cache_get("discord", "token") == {"bot_token": "tok"}
    assert plugin_kv.unsealed_rows(ana, data_crypto.PREFIX) == []
    sealed = plugin_kv.get("translation", "es", account=ana, scope="personal")[0]
    assert str(sealed).startswith(data_crypto.PREFIX)

    # A second pass (next start) finds nothing to do and leaves the rows alone.
    data_crypto.reset_for_tests()
    replaced = []
    monkeypatch.setattr(plugin_kv, "replace_value", lambda *a, **k: replaced.append(a) or True)
    assert pha.cache_get("translation", "es") == {"Hello": "Hola"}
    assert replaced == []


def test_offline_restart_uses_the_device_wrapped_key_and_sign_out_drops_it(monkeypatch) -> None:
    import json

    from backend.agent import secrets as sec
    from backend.uefn_plugins import data_crypto
    from frontend import duckyos_account
    from frontend.ui_web.agent_turns.testing import patch_turns

    patch_turns(monkeypatch, "push_ui_event", lambda event: None)
    duckyos_account._save_blob({"base_url": "https://uefnducky.org", "email": "ana@x.org", "device_key": "dky_v1_ana"})
    cards = PluginData("brainrot-tcg")
    cards.put("card.pip", {"v": 1})
    ana = scopes.account_id()
    assert data_crypto._cache_path().is_file()

    # Restart offline: memory is gone, the server can't be reached, the disk cache opens the data.
    data_crypto.reset_for_tests()
    ADK_ONLINE["on"] = False
    assert cards.get("card.pip") == {"v": 1}

    # Sign-out drops the cached key; the next account on this Windows user can't open Ana's rows.
    duckyos_account._clear_blob()
    assert not data_crypto._cache_path().exists()
    ADK_ONLINE["on"] = True
    sec.set_key("duckyos_account", json.dumps({"base_url": "https://uefnducky.org", "email": "bo@x.org",
                                              "device_key": "dky_v1_bo"}))
    assert scopes.account_id() != ana
    with pytest.raises(data_crypto.Locked):
        data_crypto.open_text(_raw_doc(ana, "card.pip"), ana)


def test_a_sign_ins_key_prefetch_is_over_before_the_next_test(monkeypatch) -> None:
    # Oct 20 2026: the prefetch a sign-in starts ran on in the next test's database (an extra
    # DPAPI call in the big-docs test, a second seal_pending inside the old-database test).
    import threading

    import conftest
    from backend.store import account_rows
    from backend.uefn_plugins import data_crypto

    monkeypatch.setattr(account_rows, "seal_pending", lambda account: time.sleep(0.5))
    data_crypto.prefetch()
    conftest._join_host_workers()  # what every test's teardown does
    assert "account-data-key" not in {t.name for t in threading.enumerate()}


def test_no_key_yet_is_read_only_and_never_writes_plaintext(who: _Who) -> None:
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import data_crypto
    from frontend.ui_web import plugin_host_api as pha

    ADK_ONLINE["on"] = False  # first sign-in, offline
    cy = who.be("cy@x.org")
    assert scopes.active_scope("brainrot-tcg")["state"] == "locked" and scopes.active_scope("brainrot-tcg")["readOnly"]
    with pytest.raises(scopes.ReadOnlyScope, match="data key"):
        PluginData("brainrot-tcg").put("card.pip", {"v": 1})
    with pytest.raises(data_crypto.Locked):
        pha.cache_set("brainrot-tcg", "ui", {"tab": "cards"})
    assert repo.rows(cy, "personal", "brainrot-tcg", "doc") == []
    assert plugin_kv.get("brainrot-tcg", "ui", account=cy, scope="personal") == (None, False)


def test_rewritten_big_docs_are_not_kept_open_in_memory(monkeypatch) -> None:
    import gc
    import json
    import tracemalloc

    from backend.agent import secrets as sec
    from backend.uefn_plugins import data_crypto

    local = data_crypto._LOCAL
    data_crypto.open_text(data_crypto.seal_text("{}", local), local)  # one-time row sealing first
    calls = []
    real = sec.unprotect_bytes
    monkeypatch.setattr(sec, "unprotect_bytes", lambda *a: calls.append(1) or real(*a))
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        for n in range(20):  # one big doc saved and read 20 times: a new ciphertext each save
            doc = json.dumps({"save": n, "cards": "x" * 300_000})
            sealed = data_crypto.seal_text(doc, local)
            assert data_crypto.open_text(sealed, local) == doc
            assert data_crypto.open_text(sealed, local) == doc
        del doc, sealed
        gc.collect()
        grown = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    assert grown < 8 * 1024 * 1024  # every old version kept: about 14 MB
    assert len(calls) == 20  # the current version is still read from memory

    # A small row read again skips the DPAPI call too.
    calls.clear()
    small = data_crypto.seal_text('{"tab": "cards"}', local)
    assert data_crypto.open_text(small, local) == data_crypto.open_text(small, local) == '{"tab": "cards"}'
    assert len(calls) == 1


def test_ids_and_paths_cannot_climb() -> None:
    assert scopes.valid_asset_path("assets/cards/pip.png")
    for bad in ("../x", "a/../b", ".hidden", "a//b", "A.png", "a\\b", "x" * 257):
        assert not scopes.valid_asset_path(bad), bad
    assert not scopes.valid_doc_key("a/b") and not scopes.valid_space_id("personal")
    assert hashlib.sha256(scopes.encode_doc({"b": 1, "a": 2})).hexdigest() == hashlib.sha256(
        b'{"a":2,"b":1}').hexdigest()


def test_a_plugin_test_hides_nothing_from_everyone_else(who: _Who) -> None:
    # Oct 12 2026: the test copy was process-wide, so shared projects and requests
    # "disappeared" for everyone whenever one person ran the full plugin test.
    import contextvars
    import threading
    from concurrent.futures import ThreadPoolExecutor

    who.be("ana@x.org")
    cards = PluginData("brainrot-tcg")
    cards.put("card.pip", {"v": 1})
    pool = ThreadPoolExecutor(max_workers=1)
    keep_going, seen_after = threading.Event(), []
    with scopes.sandbox("brainrot-tcg") as box:
        # Another agent's call (its own context) still reads the real data.
        assert contextvars.Context().run(lambda: cards.get("card.pip")) == {"v": 1}
        # A pool task the test submits runs on the test copy; the pool's worker isn't bound to it.
        assert pool.submit(lambda: scopes.active_scope("brainrot-tcg")["id"]).result() == box["id"]
        assert contextvars.Context().run(
            lambda: pool.submit(lambda: cards.get("card.pip")).result()) == {"v": 1}
        # A thread the test starts that outlives it reads the real data once the test is over.
        def worker() -> None:
            keep_going.wait(5)
            seen_after.append(cards.get("card.pip"))
        late = threading.Thread(target=worker)
        late.start()
    keep_going.set()
    late.join(5)
    pool.shutdown()
    assert seen_after == [{"v": 1}]
    assert scopes.sandboxed_plugins() == frozenset()
