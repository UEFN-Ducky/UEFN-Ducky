"""P3/P4 + §13 checks: plugin data scopes on one PC, sealed per account, and team
sync against a fake Store.

Plan P3: two accounts never see each other's rows or folders; switching project
switches data; signed-out ``_local`` is separate; ``sensitive`` docs never reach a
sync request; BrainrotTCG cards sync between two members and never to a third
team; paused = read-only; access lost locks the team scope, access back unlocks
it, a week later it goes; the inclusive cursor is deduped; a stale push adopts the
server copy.
§13: another account can't read the DB or files; the owner reads them again after
signing back in; old plaintext is sealed once; an offline restart works; no key
yet = read-only, never plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import sys
import time
from typing import Any

import pytest

# Every seal/open is DPAPI (plan §13): these checks need Windows.
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.uefn_plugins import scopes, team_sync
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


def team_key(team: str) -> bytes:
    """The Store's team data key: one per team, members only."""
    return hashlib.sha256(b"tdk/" + team.encode()).digest()


class FakeStore:
    """The Store's team-data contract in memory: inclusive cursor, last write wins,
    per-team membership, paused plans, team keys."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.rev: dict[str, int] = {}
        self.members: dict[str, set[str]] = {}
        self.paused: set[str] = set()
        self.blobs: dict[str, bytes] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.key_calls = 0
        self._ids = itertools.count(1)

    def transport(self, account: str) -> "_Transport":
        return _Transport(self, account)

    def view(self, team: str, item: tuple[str, str, str]) -> dict[str, Any]:
        row = self.rows[(team, *item)]
        out = {"kind": item[0], "pluginId": item[1], "key": item[2], "rev": row["rev"], "size": row["size"],
               "sha256": row["sha256"], "enc": row.get("enc", 0), "deleted": row["deleted"]}
        if not row["deleted"]:
            url = f"get://{next(self._ids)}"
            self.blobs[url] = row["blob"]
            out["getUrl"] = url
        return out


class _Transport:
    def __init__(self, store: FakeStore, account: str) -> None:
        self.s, self.account = store, account

    def collect(self, event: str, body: dict[str, Any]) -> dict[str, Any]:
        s, team = self.s, body["teamId"]
        s.requests.append({"event": event, **body})
        if self.account not in s.members.get(team, set()):
            raise team_sync.SyncError("team not found")
        if event == "team-data-key":
            # Members read it while paused too, like the rest of the team's data.
            s.key_calls += 1
            return {"key": base64.b64encode(team_key(team)).decode(), "version": 1}
        if team in s.paused:
            raise team_sync.SyncError("plan_paused: Team Private isn't active for this team.")
        if not body.get("enc") and any(r.get("enc") for k, r in s.rows.items() if k[0] == team):
            raise team_sync.SyncError("Update UEFN Ducky to use this team's data.")
        if event == "team-data-commit":
            results = []
            for c in body["commits"]:
                item = (c["kind"], c["pluginId"], c["key"])
                up = next((u for u in s.uploads.values() if u["team"] == team and u["item"] == item), None)
                row = s.rows.get((team, *item))
                if up is None or (row["rev"] if row else 0) != c["rev"]:
                    results.append({**dict(zip(("kind", "pluginId", "key"), item)), "status": "stale",
                                    "server": s.view(team, item) if row else None})
                    continue
                s.rev[team] = s.rev.get(team, 0) + 1
                blob = s.blobs[up["url"]]
                if len(blob) != up["size"] or hashlib.sha256(blob).hexdigest() != up["sha256"]:
                    raise AssertionError("the upload isn't the bytes its push described")
                s.rows[(team, *item)] = {"rev": s.rev[team], "size": len(blob), "sha256": up["sha256"],
                                         "enc": up["enc"], "deleted": False, "blob": blob}
                del s.uploads[up["url"]]
                results.append({**dict(zip(("kind", "pluginId", "key"), item)), "status": "committed",
                                "rev": s.rev[team]})
            return {"results": results, "usage": {"usedBytes": 1}}
        accepted, deleted, stale = [], [], []
        for p in body.get("pushes") or []:
            item = (p["kind"], p["pluginId"], p["key"])
            row = s.rows.get((team, *item))
            named = dict(zip(("kind", "pluginId", "key"), item))
            if p["baseRev"] != (row["rev"] if row else 0):
                stale.append({**named, "server": s.view(team, item) if row else None})
            elif p.get("delete"):
                s.rev[team] = s.rev.get(team, 0) + 1
                row.update(rev=s.rev[team], deleted=True, blob=b"", size=0)
                deleted.append({**named, "rev": s.rev[team]})
            else:
                url = f"put://{next(s._ids)}"
                s.uploads[url] = {"team": team, "item": item, "url": url, "sha256": p["sha256"], "size": p["size"],
                                  "enc": int(p.get("enc") or 0)}
                accepted.append({**named, "rev": p["baseRev"], "putUrl": url, "expiresAt": 0})
        cursor = int(body.get("cursorRev") or 0)
        changes = [s.view(team, k[1:]) for k, r in sorted(s.rows.items(), key=lambda kv: kv[1]["rev"])
                   if k[0] == team and r["rev"] >= cursor]
        return {"cursorRev": max(s.rev.get(team, 0), cursor), "more": False, "changes": changes,
                "accepted": accepted, "deleted": deleted, "stale": stale, "refused": [],
                "usage": {"usedBytes": sum(r["size"] for k, r in s.rows.items() if k[0] == team),
                          "limitBytes": 5 << 30}}

    def put(self, url: str, data: bytes) -> None:
        self.s.blobs[url] = data

    def get(self, url: str) -> bytes:
        return self.s.blobs[url]


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Every Store call the host makes (first pulls included) goes to this fake."""
    fake = FakeStore()
    monkeypatch.setattr(team_sync, "Transport", lambda: fake.transport(scopes.account_id()))
    monkeypatch.setattr(team_sync, "_FIRST_PULL_TRIED", set())
    return fake


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


def _sync(store: FakeStore, plugin: str = "brainrot-tcg") -> dict[str, Any]:
    s = scopes.active_scope(plugin)
    return team_sync.sync_team(s["account"], s["id"], force=True, transport=store.transport(s["account"]))


def _join(store: FakeStore, who: _Who, email: str, team: str, label: str, plugin: str = "brainrot-tcg") -> str:
    aid = who.be(email)
    store.members.setdefault(team, set()).add(aid)
    scopes.link_plugin(plugin, team, label=label, members=2)
    return aid


# --------------------------------------------------------------------------- scopes on one PC


def test_accounts_plugins_and_local_never_share_rows_or_folders(who: _Who, store: FakeStore) -> None:
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

    # The pick is per plugin: BrainrotTCG → team T shows the team's copy, Forge stays Local.
    store.members["teamT"] = {who.be("ana@x.org")}
    scenes.put("scene.a", {"by": "ana"})
    scopes.link_plugin("brainrot-tcg", "teamT", label="Alpha Studio")
    assert scopes.active_scope("brainrot-tcg")["label"] == "Alpha Studio" and cards.keys() == []
    assert scopes.active_scope("forge")["kind"] == "personal" and scenes.get("scene.a") == {"by": "ana"}
    cards.put("card.pip", {"name": "Team Pip"})
    scopes.link_plugin("brainrot-tcg", scopes.PERSONAL)
    assert cards.get("card.pip") == {"name": "Pip"}  # nothing moved on either switch
    assert cards.get_file("assets/pip.png") == b"PNG-ana"
    scopes.link_plugin("brainrot-tcg", "teamT")
    assert cards.get("card.pip") == {"name": "Team Pip"}
    who.be("")
    assert cards.get("card.pip") == {"name": "Local Pip"}
    with pytest.raises(ValueError, match="Sign in"):
        scopes.link_plugin("brainrot-tcg", "teamT")


def test_two_teams_of_one_account_never_share_a_plugins_data(who: _Who, store: FakeStore) -> None:
    cards = PluginData("brainrot-tcg")
    ana = _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    _join(store, who, "ana@x.org", "teamU", "Beta Crew", plugin="forge")
    _sync(store)
    _sync(store, "forge")
    cards.put("card.pip", {"team": "T"})
    _sync(store)
    # Switching BrainrotTCG to team U shows U's own (empty) copy: T's card never arrives there.
    scopes.link_plugin("brainrot-tcg", "teamU")
    assert cards.keys() == []
    cards.put("card.mo", {"team": "U"})
    _sync(store)
    pushed = {(r["teamId"], p["key"]) for r in store.requests for p in r.get("pushes") or []}
    assert pushed == {("teamT", "card.pip"), ("teamU", "card.mo")}
    scopes.link_plugin("brainrot-tcg", "teamT")
    assert cards.items() == {"card.pip": {"team": "T"}}
    assert repo.links(ana) == {"brainrot-tcg": "teamT", "forge": "teamU"}


def test_copy_local_into_a_team_is_one_way_and_keeps_the_teams_items(who: _Who, store: FakeStore) -> None:
    cards = PluginData("brainrot-tcg")
    ana = who.be("ana@x.org")
    cards.put("card.pip", {"v": "local"})
    cards.put("card.mo", {"v": "local"})
    cards.put("token", {"secret": 1}, sensitive=True)
    cards.put_file("assets/pip.png", b"PNG local")
    store.members["teamT"] = {ana}
    scopes.link_plugin("brainrot-tcg", "teamT", label="Alpha Studio")
    _sync(store)
    cards.put("card.pip", {"v": "team"})
    status = team_sync.scope_status("brainrot-tcg")
    assert status["local"] == {"docs": 2, "files": 1, "docsBytes": status["local"]["docsBytes"], "filesBytes": 9}
    assert status["current"]["docs"] == 1

    assert scopes.copy_local_into("brainrot-tcg", "teamT") == {"ok": True, "copied": 2, "kept": 1}
    assert cards.items() == {"card.mo": {"v": "local"}, "card.pip": {"v": "team"}}
    assert cards.get_file("assets/pip.png") == b"PNG local"
    assert _sync(store)["state"] == "ok"
    pushes = [p for r in store.requests for p in r.get("pushes") or []]
    pushed = sorted(p["key"] for p in pushes if p["pluginId"] == "brainrot-tcg")
    assert pushed == ["assets/pip.png", "card.mo", "card.pip"] and "secret" not in repr(store.requests)
    personal = team_sync.share_plugin_id(ana, "brainrot-tcg")
    assert sorted(p["key"] for p in pushes if p["pluginId"] == personal) == ["card.mo", "card.pip"]
    # The Local copy is untouched.
    scopes.link_plugin("brainrot-tcg", scopes.PERSONAL)
    assert cards.items() == {"card.mo": {"v": "local"}, "card.pip": {"v": "local"}}


def test_sensitive_docs_stay_personal_and_never_reach_a_sync_request(who: _Who, store: FakeStore, monkeypatch) -> None:
    from backend.agent import secrets as sec

    monkeypatch.setattr(sec, "protect_text", lambda s: b"blob-" + s.encode())
    monkeypatch.setattr(sec, "unprotect_text", lambda b: b[5:].decode())
    _join(store, who, "ana@x.org", "teamT", "Alpha Studio", plugin="discord")
    data = PluginData("discord")
    data.put("token", {"bot": "very-secret"}, sensitive=True)
    data.put("channel", {"id": 7})
    assert data.get("token", sensitive=True) == {"bot": "very-secret"}
    assert "token" not in data.keys()
    assert _sync(store, "discord")["state"] == "ok"
    pushed = [p["key"] for r in store.requests for p in r.get("pushes") or []]
    assert pushed == ["channel"] and "very-secret" not in repr(store.requests)


# --------------------------------------------------------------------------- team sync


def test_cards_sync_between_members_and_never_to_another_team(who: _Who, store: FakeStore) -> None:
    cards = PluginData("brainrot-tcg")
    _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    cards.put("card.pip", {"name": "Pip", "attack": 1700})
    cards.put_file("assets/pip.png", b"\x89PNG pip")
    assert _sync(store)["state"] == "ok"
    assert repo.count_dirty(scopes.account_id(), "teamT") == 0

    _join(store, who, "bo@x.org", "teamT", "Alpha Studio")
    out = _sync(store)
    assert out["changed"] == ["brainrot-tcg"]
    assert cards.get("card.pip") == {"attack": 1700, "name": "Pip"}
    assert cards.get_file("assets/pip.png") == b"\x89PNG pip"

    # Bo edits and deletes; Ana gets both.
    cards.put("card.pip", {"name": "Pip", "attack": 1900})
    cards.delete_file("assets/pip.png")
    _sync(store)
    who.be("ana@x.org")
    _sync(store)
    assert cards.get("card.pip")["attack"] == 1900 and cards.get_file("assets/pip.png") is None

    # A member of team U only never sees team T's cards, and can't ask for team T.
    cy = _join(store, who, "cy@x.org", "teamU", "Beta Crew")
    _sync(store)
    assert cards.keys() == [] and cards.files() == []
    assert team_sync.sync_team(cy, "teamT", force=True, transport=store.transport(cy))["state"] == "removed"
    assert repo.rows(cy, "teamT", "brainrot-tcg", "doc") == []


def test_inclusive_cursor_is_deduped_and_stale_push_adopts_server(who: _Who, store: FakeStore) -> None:
    cards = PluginData("brainrot-tcg")
    ana = _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    _sync(store)
    before = repo.get(ana, "teamT", "brainrot-tcg", "doc", "card.pip")
    # The next round gets card.pip again (rev >= cursor): nothing re-downloaded or changed.
    assert _sync(store)["changed"] == []
    assert repo.get(ana, "teamT", "brainrot-tcg", "doc", "card.pip") == before

    # Bo writes v2 first; Ana's offline edit (base rev 1) is stale → Ana adopts v2.
    _join(store, who, "bo@x.org", "teamT", "Alpha Studio")
    _sync(store)
    cards.put("card.pip", {"v": 2})
    _sync(store)
    who.be("ana@x.org")
    cards.put("card.pip", {"v": "ana-offline"})
    assert _sync(store)["changed"] == ["brainrot-tcg"]
    assert cards.get("card.pip") == {"v": 2} and repo.count_dirty(ana, "teamT") == 0


def test_paused_is_read_only_and_access_lost_locks_then_deletes_the_team_scope(who: _Who, store: FakeStore) -> None:
    from backend.store.repos import plugin_kv
    from backend.uefn_plugins import team_keys
    from frontend.ui_web import plugin_host_api as host_api

    cards = PluginData("brainrot-tcg")
    ana = _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    cards.put("card.pip", {"v": 1})
    cards.put_file("assets/pip.png", b"pip")
    # Cache and prefs follow the team copy too (kept on this PC, never synced).
    host_api.cache_set("brainrot-tcg", "deck", {"n": 1})
    host_api.prefs_plugin_set("brainrot-tcg", {"sort": "name"})
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope="teamT") == ["deck"]
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope=scopes.PERSONAL) == []
    _sync(store)
    team_dir = scopes.scopes_root(ana) / "teamT"
    assert team_dir.is_dir()

    store.paused.add("teamT")
    assert _sync(store)["state"] == "paused"
    assert scopes.active_scope("brainrot-tcg")["readOnly"] and cards.get("card.pip") == {"v": 1}
    with pytest.raises(scopes.ReadOnlyScope):
        cards.put("card.pip", {"v": 2})
    store.paused.clear()
    assert _sync(store)["state"] == "ok" and not scopes.active_scope("brainrot-tcg")["readOnly"]

    # Access lost: the key goes and the copy here locks (unreadable, read-only), kept as it is.
    store.members["teamT"].discard(ana)
    assert _sync(store)["state"] == "lost"
    assert (ana, "teamT") not in team_keys._KEYS
    scope = scopes.active_scope("brainrot-tcg")
    assert (scope["kind"], scope["state"], scope["readOnly"]) == ("team", "lost", True)
    assert cards.get("card.pip") is None and cards.keys() == [] and cards.get_file("assets/pip.png") is None
    with pytest.raises(scopes.ReadOnlyScope, match="no longer have access"):
        cards.put("card.pip", {"v": 3})
    assert host_api.cache_get("brainrot-tcg", "deck") == {} and host_api.prefs_plugin_get("brainrot-tcg") == {}
    with pytest.raises(scopes.ReadOnlyScope):
        host_api.cache_set("brainrot-tcg", "deck", {"n": 2})
    assert team_dir.is_dir() and repo.rows(ana, "teamT", "brainrot-tcg", "doc")
    assert team_sync.scope_status("brainrot-tcg")["deleteAt"] > 0

    # Access back: the key is fetched again, the data unlocks and sync resumes.
    store.members["teamT"].add(ana)
    assert _sync(store)["state"] == "ok" and cards.get("card.pip") == {"v": 1}
    assert host_api.cache_get("brainrot-tcg", "deck") == {"n": 1}
    assert host_api.prefs_plugin_get("brainrot-tcg") == {"sort": "name"}
    assert not scopes.lost_since(ana, "teamT")

    # A week without access: the copy on this PC goes (the server's stays).
    store.members["teamT"].discard(ana)
    _sync(store)
    scopes.clear_lost(ana, "teamT")
    scopes.mark_lost(ana, "teamT", since=time.time() - scopes.LOST_KEEP_S - 60)
    assert scopes.active_scope("brainrot-tcg")["kind"] == "personal"  # the plugin's link went with it
    assert not team_dir.exists() and repo.rows(ana, "teamT", "brainrot-tcg", "doc") == []
    assert plugin_kv.keys("brainrot-tcg", account=ana, scope="teamT") == []
    assert plugin_kv.all_prefs(account=ana, scope="teamT") == {}
    assert repo.links(ana) == {}
    assert ("teamT", "doc", "brainrot-tcg", "card.pip") in store.rows


def test_a_plugin_test_runs_in_a_sandbox_that_never_touches_the_users_data(who: _Who) -> None:
    from frontend.ui_web import plugin_host_api as host_api

    who.be("ana@x.org")
    cards = PluginData("brainrot-tcg")
    cards.put("card.pip", {"v": 1})
    host_api.cache_set("brainrot-tcg", "deck", {"n": 1})
    with scopes.sandbox("brainrot-tcg") as box:
        assert scopes.active_scope("brainrot-tcg")["id"] == box["id"] == scopes.SANDBOX_SCOPE
        assert cards.get("card.pip") is None and host_api.cache_get("brainrot-tcg", "deck") == {}
        cards.put("card.junk", {"v": 0})
        host_api.cache_set("brainrot-tcg", "deck", {"n": 0})
    assert cards.keys() == ["card.pip"] and host_api.cache_get("brainrot-tcg", "deck") == {"n": 1}
    assert repo.rows(scopes.account_id(), scopes.SANDBOX_SCOPE, "brainrot-tcg", "doc") == []


def test_team_data_leaves_the_pc_encrypted_with_its_teams_own_key(who: _Who, store: FakeStore) -> None:
    from backend.uefn_plugins import team_keys

    cards = PluginData("brainrot-tcg")
    ana = _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    cards.put("card.pip", {"name": "Pip"})
    cards.put_file("assets/pip.png", b"\x89PNG pip")
    assert _sync(store)["state"] == "ok"
    doc = store.rows[("teamT", "doc", "brainrot-tcg", "card.pip")]
    png = store.rows[("teamT", "asset", "brainrot-tcg", "assets/pip.png")]
    for row in (doc, png):
        assert row["enc"] == 1 and row["blob"].startswith(team_keys.MAGIC) and b"Pip" not in row["blob"]
    # The server's size and sha256 are the stored (encrypted) bytes; the local row keeps the plaintext's.
    local = repo.get(ana, "teamT", "brainrot-tcg", "doc", "card.pip")
    assert doc["sha256"] == hashlib.sha256(doc["blob"]).hexdigest() != local["sha256"]
    assert local["sha256"] == hashlib.sha256(scopes.encode_doc({"name": "Pip"})).hexdigest()
    # Only that team's key opens it, and only as the item and team it was written for.
    item = ("doc", "brainrot-tcg", "card.pip")
    tk = (1, team_key("teamT"))
    assert team_keys.open_object(tk, "teamT", item, doc["blob"]) == scopes.encode_doc({"name": "Pip"})
    with pytest.raises(ValueError):
        team_keys.open_object((1, team_key("teamU")), "teamT", item, doc["blob"])
    with pytest.raises(ValueError):
        team_keys.open_object(tk, "teamT", ("doc", "brainrot-tcg", "card.mo"), doc["blob"])
    with pytest.raises(ValueError):
        team_keys.open_object(tk, "teamU", item, doc["blob"])
    # The same item and bytes always encrypt to the same object (a push names it before the upload).
    assert team_keys.seal(tk, "teamT", item, b"x") == team_keys.seal(tk, "teamT", item, b"x")
    assert team_keys.seal(tk, "teamT", item, b"x")[5:17] != team_keys.seal(tk, "teamT", item, b"y")[5:17]
    # Sync and commit requests say this app opens encrypted items.
    assert all(r.get("enc") == team_keys.FORMAT for r in store.requests if r["event"] != "team-data-key")
    # The key is asked of the Store once, then kept.
    calls = store.key_calls
    _sync(store)
    assert calls == 1 and store.key_calls == calls


def test_plaintext_from_before_team_keys_still_reads_and_goes_up_again_encrypted(who: _Who, store: FakeStore) -> None:
    cards = PluginData("brainrot-tcg")
    ana = _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    # The team's copy as an older app left it: plaintext, key version 0.
    legacy = scopes.encode_doc({"name": "Old"})
    store.rev["teamT"] = 1
    store.rows[("teamT", "doc", "brainrot-tcg", "card.old")] = {
        "rev": 1, "size": len(legacy), "sha256": hashlib.sha256(legacy).hexdigest(), "enc": 0, "deleted": False,
        "blob": legacy,
    }
    assert _sync(store)["state"] == "ok"
    assert cards.get("card.old") == {"name": "Old"}
    row = store.rows[("teamT", "doc", "brainrot-tcg", "card.old")]
    assert row["enc"] == 1 and row["rev"] > 1 and b"Old" not in row["blob"]
    assert repo.count_dirty(ana, "teamT") == 0
    # Once: later rounds push nothing.
    store.requests.clear()
    _sync(store)
    assert not any(r.get("pushes") for r in store.requests)


def test_scope_picker_lists_only_active_team_private_and_hides_without_beta(who: _Who, monkeypatch) -> None:
    from frontend import duckyos_account

    who.be("ana@x.org")
    teams = [
        {"id": "teamT", "name": "Alpha Studio", "members": [{}, {}, {}], "private_plan": {"status": "active"}},
        {"id": "teamF", "name": "Free Team", "members": [{}], "private_plan": None},
        {"id": "teamP", "name": "Paused Crew", "members": [{}], "private_plan": {"status": "paused"}},
    ]
    monkeypatch.setattr(duckyos_account, "teams_snapshot", lambda **_: {"ok": True, "teams": teams})
    monkeypatch.setattr(team_sync, "teams_enabled", lambda: False)
    assert [c["id"] for c in team_sync.scope_choices()["choices"]] == ["personal"]
    assert team_sync.scope_status("brainrot-tcg")["visible"] is False  # rule 13: no bar, no teaser
    monkeypatch.setattr(team_sync, "teams_enabled", lambda: True)
    choices = team_sync.scope_choices()["choices"]
    assert [(c["id"], c.get("members")) for c in choices] == [("personal", None), ("teamT", 3)]
    status = team_sync.link_scope("brainrot-tcg", "teamT")
    # Fresh link: read-only "waiting" until the first pull lands.
    assert status["scope"] == {"kind": "team", "label": "Alpha Studio", "teamId": "teamT", "readOnly": True}
    assert status["visible"] and status["state"] == "waiting" and status["members"] == 3 and status["pending"] == 0


class _Offline:
    def collect(self, event: str, body: dict[str, Any]) -> dict[str, Any]:
        raise team_sync.SyncError("Network error: offline", offline=True)


def test_first_use_of_a_team_pulls_before_a_plugin_can_seed_defaults(who: _Who, store: FakeStore, monkeypatch) -> None:
    def load_or_seed(data: PluginData) -> dict[str, Any]:
        """What BrainrotTCG's load_db does: an empty scope gets the bundled defaults."""
        docs = data.items()
        if not docs:
            data.put("meta", {"seeded": True})
            data.put("card.default", {"name": "Seed"})
            return data.items()
        return docs

    cards = PluginData("brainrot-tcg")
    _join(store, who, "ana@x.org", "teamT", "Alpha Studio")
    cards.put("meta", {"team": True})
    cards.put("card.pip", {"name": "Pip"})
    _sync(store)

    # Bo links the team for the first time: the first pull lands before the plugin reads.
    _join(store, who, "bo@x.org", "teamT", "Alpha Studio")
    assert load_or_seed(cards) == {"card.pip": {"name": "Pip"}, "meta": {"team": True}}

    # Cy's first use is offline: read-only "waiting", the seed is refused, nothing reaches the team.
    cy = _join(store, who, "cy@x.org", "teamT", "Alpha Studio")
    monkeypatch.setattr(team_sync, "Transport", _Offline)
    with pytest.raises(scopes.ReadOnlyScope, match="Waiting for team data"):
        load_or_seed(cards)
    assert scopes.active_scope("brainrot-tcg")["state"] == "waiting"
    # Back online, the minute sync brings the team's copy and the scope opens.
    assert team_sync.sync_team(cy, "teamT", force=True, transport=store.transport(cy))["state"] == "ok"
    assert load_or_seed(cards)["card.pip"] == {"name": "Pip"}
    assert not scopes.active_scope("brainrot-tcg")["readOnly"]
    assert not any(k[3] == "card.default" for k in store.rows)


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
    from frontend.ui_web import agent_modes

    monkeypatch.setattr(agent_modes, "push_ui_event", lambda event: None)
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


def test_ids_and_paths_cannot_climb() -> None:
    assert scopes.valid_asset_path("assets/cards/pip.png")
    for bad in ("../x", "a/../b", ".hidden", "a//b", "A.png", "a\\b", "x" * 257):
        assert not scopes.valid_asset_path(bad), bad
    assert not scopes.valid_doc_key("a/b") and not scopes.valid_team_id("personal")
    assert hashlib.sha256(scopes.encode_doc({"b": 1, "a": 2})).hexdigest() == hashlib.sha256(
        b'{"a":2,"b":1}').hexdigest()
