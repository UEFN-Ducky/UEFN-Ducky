"""Live editing in core: the WebRTC bridge to the main window (asks and answers, bytes in
and out, only for the spaces provider), going live on a team copy only, in groups named
after their host, what goes to the group (the host's pointers and holds, a plugin's own
topics within limits), and changes: never live, only a commit's "pull now", which pulls
at once. Presence carries the room; panel pages get the live script.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

from backend.store.repos import plugin_data as repo
from backend.store.repos import space_vcs
from backend.uefn_plugins import data_crypto, live, rtc, scopes, spaces, vcs_engine
from backend.uefn_plugins.scopes import PluginData

SPACE, PLUGIN, OTHER = "spaceT", "cards", "notes"
ALEX = {"name": "Alex", "user": "u_alex", "device": "d_alex", "label": "ALEX-PC"}


class FakeLive(spaces.Provider):
    kind_label = "team"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any, str, bool]] = []
        self.joined: list[tuple[str, str, str | None]] = []
        self.peers: list[dict[str, Any]] = []
        self.supported = True

    def enabled(self) -> bool:
        return True

    def live_supported(self) -> bool:
        return self.supported

    def live_join(self, account: str, space_id: str, plugin: str, room: str) -> None:
        self.joined.append((space_id, plugin, room))

    def live_leave(self, account: str, space_id: str, plugin: str) -> None:
        self.joined.append((space_id, plugin, None))

    def live_send(self, account: str, space_id: str, plugin: str, topic: str, data: Any, *,
                  to: str = "", fast: bool = False) -> int:
        self.sent.append((plugin, topic, data, to, fast))
        return 1

    def live_state(self, account: str, space_id: str, plugin: str) -> dict[str, Any]:
        return {"state": "live", "peers": list(self.peers)}


@pytest.fixture
def team(monkeypatch: pytest.MonkeyPatch):
    """Ana signed in, ``cards`` on the team copy (pulled once), a provider with live."""
    from frontend import duckyos_account

    key = "https://uefnducky.org|ana@example.com"
    data_crypto.reset_for_tests()
    monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: key)
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: hashlib.sha256(b"adk/" + key.encode()).digest())
    impl = FakeLive()
    spaces.install_for_tests(impl)
    account = scopes.account_id()
    repo.link_set(account, PLUGIN, SPACE)
    repo.sync_put(account, SPACE, label="Alpha", synced_at=time.time())
    space_vcs.head_put(account, SPACE, PLUGIN, engine="v2")  # on the team repo: presence, locks, live
    pushed: list[dict[str, Any]] = []
    beats: list[tuple[str, str]] = []
    monkeypatch.setattr(live, "_push", pushed.append)
    monkeypatch.setattr(live, "_beat_soon", lambda a, s: beats.append((a, s)))
    woke: list[tuple[str, str, str | None]] = []
    from backend.uefn_plugins import sync_scheduler

    monkeypatch.setattr(sync_scheduler, "wake", lambda a, s, p=None: woke.append((a, s, p)))
    monkeypatch.setattr(live, "_enabled", lambda plugin: True)
    live.reset_for_tests()
    yield {"account": account, "impl": impl, "pushed": pushed, "beats": beats, "woke": woke}
    live.reset_for_tests()
    spaces.reset_for_tests()
    scopes.forget_change_listeners(PLUGIN)
    data_crypto.reset_for_tests()


def _row(account: str, key: str) -> dict[str, Any] | None:
    return repo.get(account, SPACE, PLUGIN, "doc", key)


# --------------------------------------------------------------------------- the bridge


def test_the_bridge_asks_the_window_and_waits_for_its_answer() -> None:
    told: list[dict[str, Any]] = []
    b = rtc.Bridge(push=told.append)
    offer = {"type": "offer", "sdp": "v=0 offer"}

    def answer() -> None:
        while not told:
            time.sleep(0.01)
        b.reply(told[0]["id"], {"offer": offer})

    threading.Thread(target=answer, daemon=True).start()
    assert b.open("peer-1") == offer
    assert told[0]["type"] == "rtc_cmd" and told[0]["op"] == "open" and told[0]["peer"] == "peer-1"
    assert told[0]["ice"] == rtc.DEFAULT_ICE
    assert b.reply(told[0]["id"], {}) is False  # nobody waits for it any more


def test_the_bridge_says_why_when_the_window_fails_or_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[dict[str, Any]] = []
    b = rtc.Bridge(push=lambda ev: (told.append(ev), b.reply(ev["id"], {"error": "no such peer"})))
    with pytest.raises(rtc.RtcError, match="no such peer"):
        b.finish("peer-1", {"type": "answer", "sdp": "v=0"})
    quiet = rtc.Bridge(push=lambda ev: None)
    with pytest.raises(rtc.RtcError, match="window"):
        quiet._ask("open", timeout=0.05, peer="p")
    with pytest.raises(rtc.RtcError, match="invalid"):
        quiet.accept("p", {"type": "answer", "sdp": "x"})  # not an offer
    with pytest.raises(rtc.RtcError, match="peer id"):
        quiet.open("bad peer!")


def test_bytes_go_out_as_base64_and_come_back_as_bytes() -> None:
    told: list[dict[str, Any]] = []
    b = rtc.Bridge(push=told.append)
    b.send("peer-1", "live-fast", b"\x00\x01hi")
    assert told[-1] == {"type": "rtc_cmd", "op": "send", "peer": "peer-1", "channel": "live-fast",
                        "data": base64.b64encode(b"\x00\x01hi").decode()}
    with pytest.raises(rtc.RtcError):
        b.send("peer-1", "other", b"x")
    with pytest.raises(rtc.RtcError):
        b.send("peer-1", "live", b"x" * (rtc.MAX_SEND_BYTES + 1))
    got: list[tuple[str, ...]] = []
    b.on_message = lambda peer, ch, data: got.append((peer, ch, data))
    b.on_state = lambda peer, st: got.append((peer, st))
    b.event("peer-1", "message", {"channel": "live", "data": base64.b64encode(b"yo").decode()})
    b.event("peer-1", "message", {"channel": "nope", "data": "eW8="})
    b.event("peer-1", "message", {"channel": "live", "data": "not base64!"})
    b.event("peer-1", "state", {"state": "connected"})
    b.event("peer-1", "state", {"state": "weird"})
    b.event("bad peer!", "state", {"state": "connected"})
    assert got == [("peer-1", "live", b"yo"), ("peer-1", "connected")]


def test_only_the_spaces_provider_gets_the_bridge(team: dict[str, Any]) -> None:
    with pytest.raises(PermissionError):
        spaces.rtc_bridge("cards")
    with pytest.raises(PermissionError):
        spaces.rtc_bridge("")
    assert spaces.rtc_bridge("test-spaces") is rtc.bridge()


# --------------------------------------------------------------------------- going live


def test_live_is_for_a_team_copy_with_a_provider_that_has_it(team: dict[str, Any]) -> None:
    impl, account = team["impl"], team["account"]
    impl.supported = False
    assert live.join(PLUGIN)["ok"] is False
    impl.supported = True
    space_vcs.head_put(account, SPACE, PLUGIN, engine="v1")
    assert live.join(PLUGIN)["error"].startswith("Live works once")  # the older sync has no presence
    space_vcs.head_put(account, SPACE, PLUGIN, engine="v2")
    repo.link_set(account, PLUGIN, scopes.PERSONAL)
    got = live.join(PLUGIN)
    assert got == {"ok": False, "error": "Live works on a team copy."}
    repo.link_set(account, PLUGIN, SPACE)
    assert live.join(PLUGIN, "no spaces!")["ok"] is False
    got = live.join(PLUGIN)
    me = space_vcs.device_id()
    assert got["ok"] and got["on"] and got["room"] == f"h:{me}" and got["state"] == "live"  # this PC hosts
    assert impl.joined == [(SPACE, PLUGIN, f"h:{me}")]
    assert team["beats"] == [(account, SPACE)]  # presence says so now, not at the next beat
    assert team["woke"] == [(account, SPACE, PLUGIN)]  # joined up to date: a pull now
    told = [e for e in team["pushed"] if e["type"] == "live_peers"]
    assert told[-1]["on"] is True
    assert team["pushed"][-1] == {"type": "live_team", "account": account, "space": SPACE, "on": True}


def test_presence_carries_the_room_and_leaving_drops_it(team: dict[str, Any]) -> None:
    account = team["account"]
    live.join(PLUGIN, "level-2")
    blob = vcs_engine.presence_blob(account, SPACE, [PLUGIN])
    assert blob["plugins"][PLUGIN]["live"]["room"] == "level-2"
    status = spaces.scope_status(PLUGIN)
    assert status["live"]["on"] is True and status["live"]["room"] == "level-2"
    live.join(PLUGIN, "level-3")  # another room: the first one is left
    assert team["impl"].joined[-2:] == [(SPACE, PLUGIN, None), (SPACE, PLUGIN, "level-3")]
    live.leave_live(PLUGIN)
    assert "live" not in vcs_engine.presence_blob(account, SPACE, [PLUGIN])["plugins"][PLUGIN]
    assert spaces.scope_status(PLUGIN)["live"]["on"] is False


def _roster(account: str, *people: dict[str, Any]) -> None:
    space_vcs.presence_put(account, SPACE, {"people": list(people)}, sent_sha="", now=time.time())


def _on(device: str, name: str, room: str, since: float, plugin: str = PLUGIN, *,
        app: str = "") -> dict[str, Any]:
    """A teammate live in ``room``, on an app as new as this one, or on ``app``: "1.2.438"
    (live version 2: it relays, and streams only plugins that ask for it), "1.2.434" (relays,
    says no version at all) or "1.2.433" (relays nothing; its team service says its version)."""
    v = {"": live.PROTOCOL, "1.2.438": 2}.get(app, 0)
    row = {"room": room, "since": since, **({"v": v} if v else {})}
    return {"device": device, "user": f"u_{name}", "user_name": name, "label": f"{name.upper()}-PC",
            **({"ver": app} if app in ("1.2.433", "1.2.438") else {}),
            "plugins": {plugin: {"viewing": True, "live": row}}}


def test_groups_are_named_by_their_host_with_who_is_in_them(team: dict[str, Any]) -> None:
    account = team["account"]
    _roster(account,
            _on("d_iliya", "iliya", "h:d_iliya", 10), _on("d_cy", "Cy", "h:d_iliya", 20),
            _on("d_mo", "Mo", "h:d_mo", 5), _on("d_ed", "Ed", "h:d_gone", 30),
            _on("d_x", "Xi", "h:d_x", 1, plugin="other"),
            {"device": "d_bo", "user_name": "Bo", "plugins": {PLUGIN: {"viewing": True}}})
    got = live.status(PLUGIN)["groups"]
    assert [(g["room"], g["host"], [m["name"] for m in g["members"]], g["mine"]) for g in got] == [
        ("h:d_iliya", "iliya", ["iliya", "Cy"], False),  # the biggest first
        ("h:d_gone", "Ed", ["Ed"], False),  # its host left: whoever has been in it longest leads it
        ("h:d_mo", "Mo", ["Mo"], False),
    ]
    assert got[1]["hostDevice"] == "d_ed"
    # Joining iliya's group: this PC counts at once, before its next beat, and comes first.
    assert live.join(PLUGIN, "h:d_iliya")["ok"]
    mine = live.status(PLUGIN)["groups"][0]
    assert mine["mine"] and mine["host"] == "iliya" and not mine["hostIsMe"]
    assert [m["name"] for m in mine["members"]] == ["iliya", "Cy", "You"] and mine["members"][2]["me"]
    # Hosting: this PC's own group, once nobody else is live.
    live.leave_live(PLUGIN)
    _roster(account)
    assert live.join(PLUGIN)["ok"]
    mine = live.status(PLUGIN)["groups"][0]
    assert mine["room"] == live.host_room() and mine["hostIsMe"] and mine["host"] == "You"


def test_start_live_goes_into_a_teammates_group_instead_of_a_second_one(team: dict[str, Any]) -> None:
    account = team["account"]
    _roster(account, _on("d_mo", "Mo", "h:d_mo", 5), _on("d_iliya", "iliya", "h:d_iliya", 10),
            _on("d_cy", "Cy", "h:d_iliya", 20))
    got = live.join(PLUGIN)  # Start live
    assert got["ok"] and got["room"] == "h:d_iliya"  # the biggest group
    assert team["impl"].joined == [(SPACE, PLUGIN, "h:d_iliya")]
    assert live.keeps_panels(PLUGIN) is False  # joined like Join: the first pull opens the group's data
    assert live.join(PLUGIN)["room"] == "h:d_iliya"  # pressed again: still there, never a group of one


def test_online_teammates_can_be_invited_and_the_invite_pops_once_for_30_seconds(team: dict[str, Any]) -> None:
    """Online = in the roster, not in this PC's group. Invite starts live when not live yet,
    rides presence, and the invited PC's roster pops one Join question while it stands."""
    account, me = team["account"], space_vcs.device_id()
    idle = {"device": "d_bo", "user": "u_bo", "user_name": "Bo", "plugins": {PLUGIN: {"viewing": True}}}
    _roster(account, _on("d_mo", "Mo", "h:d_mo", 5), idle)
    people = live.online(account, SPACE)
    assert [(p["name"], p["live"], p["invited"]) for p in people] == [("Bo", False, False), ("Mo", True, False)]
    got = live.invite(account, SPACE, "d_bo")
    assert got["ok"] and live.team_room(account, SPACE)["room"] == "h:d_mo"  # Start live came first
    assert [i["to"] for i in vcs_engine.presence_blob(account, SPACE, [PLUGIN])["invites"]] == ["d_bo"]
    assert next(p for p in live.online(account, SPACE) if p["device"] == "d_bo")["invited"] is True
    assert live.invites_of(account, SPACE, now=time.time() + live.INVITE_S + 1) == []  # runs out
    assert live.invite(account, SPACE, me)["ok"] is False  # never this PC

    # The other side: Mo invites this PC into Mo's group.
    live.leave_team(account, SPACE)
    t = time.time()
    mo = {**_on("d_mo", "Mo", "h:d_mo", 5), "invites": [{"to": me, "room": "h:d_mo", "at": t - 10},
                                                       {"to": "d_x", "room": "h:d_mo", "at": t}]}
    told = live.roster_invites(account, SPACE, {"people": [mo]}, now=t)
    assert [(e["from"], e["room"], round(e["expires"] - t)) for e in told] == [("Mo", "h:d_mo", 20)]
    assert [e for e in team["pushed"] if e["type"] == "live_invite"] == told
    assert live.roster_invites(account, SPACE, {"people": [mo]}, now=t) == []  # once
    late = {**mo, "invites": [{"to": me, "room": "h:d_mo", "at": t - live.INVITE_S - 1}]}
    assert live.roster_invites(account, SPACE, {"people": [late]}, now=t) == []  # ran out
    assert live.join_team(account, SPACE, "h:d_mo")["ok"]
    here = {**mo, "invites": [{"to": me, "room": "h:d_mo", "at": t + 1}]}
    assert live.roster_invites(account, SPACE, {"people": [here]}, now=t) == []  # already in that group


def _wait_room(room: str) -> None:
    deadline = time.monotonic() + 5
    while live.status(PLUGIN)["room"] != room and time.monotonic() < deadline:
        time.sleep(0.02)


def test_two_lone_hosts_merge_and_only_the_higher_id_moves(team: dict[str, Any],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    account = team["account"]
    monkeypatch.setattr(live, "_device", lambda: "d_m")
    _roster(account)
    assert live.join(PLUGIN)["room"] == "h:d_m"  # nobody else live yet: this PC hosts
    # Someone with a higher id started at the same moment: they come here, this PC stays.
    _roster(account, _on("d_z", "Zed", "h:d_z", 10))
    assert live.merge_lone_groups(account, SPACE) == []
    # A lower id: this PC moves into theirs, its panels kept as they are.
    lone = _on("d_a", "Ana", "h:d_a", 10)
    _roster(account, lone)
    vcs_engine._roster_came(account, SPACE, {}, {"people": [lone]})
    _wait_room("h:d_a")
    assert live.status(PLUGIN)["room"] == "h:d_a"
    assert PLUGIN not in live._join_reload  # moved with its panels as they are: no first-pull reload
    assert live.merge_lone_groups(account, SPACE) == []  # not hosting any more


def test_a_group_with_someone_in_it_never_moves_and_a_lone_host_goes_to_the_biggest(
        team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    account = team["account"]
    monkeypatch.setattr(live, "_device", lambda: "d_m")
    _roster(account)
    live.join(PLUGIN)
    _roster(account, _on("d_b", "Bo", "h:d_m", 20), _on("d_a", "Ana", "h:d_a", 10))
    assert live.merge_lone_groups(account, SPACE) == []  # Bo is in this PC's group
    _roster(account, _on("d_z", "Zed", "h:d_z", 10), _on("d_y", "Yu", "h:d_z", 12), _on("d_a", "Ana", "h:d_a", 10))
    assert live.merge_lone_groups(account, SPACE) == [PLUGIN]
    _wait_room("h:d_z")
    assert live.status(PLUGIN)["room"] == "h:d_z"  # two people beat a lower id


def test_the_host_leaving_passes_the_group_to_whoever_was_in_it_longest(team: dict[str, Any]) -> None:
    account = team["account"]
    _roster(account, _on("d_iliya", "iliya", "h:d_iliya", 10), _on("d_cy", "Cy", "h:d_iliya", 20))
    live.join(PLUGIN, "h:d_iliya")
    _roster(account, _on("d_cy", "Cy", "h:d_iliya", 20))  # iliya left
    mine = live.status(PLUGIN)["groups"][0]
    assert (mine["host"], mine["hostDevice"], mine["hostIsMe"]) == ("Cy", "d_cy", False)
    _roster(account)  # Cy left too: this PC leads what is left of it
    mine = live.status(PLUGIN)["groups"][0]
    assert mine["hostIsMe"] and mine["room"] == "h:d_iliya"


def test_live_ends_when_everyone_else_left_and_the_panels_keep_syncing(team: dict[str, Any]) -> None:
    account = team["account"]
    _roster(account, _on("d_iliya", "iliya", "h:d_iliya", 10))
    live.join(PLUGIN, "h:d_iliya")
    live.remember_team(account, SPACE, "h:d_iliya")
    assert live.end_lone_groups(account, SPACE) == []  # iliya is here
    # iliya is still on the roster but not live any more: they left, so Live ends at once.
    _roster(account, {"device": "d_iliya", "user_name": "iliya", "plugins": {PLUGIN: {"viewing": True}}})
    team["pushed"].clear()
    assert live.end_lone_groups(account, SPACE) == [PLUGIN]
    assert live.is_live(PLUGIN) is False and live.status(PLUGIN)["rejoin"] == ""
    assert {"type": "live_peers", "plugin": PLUGIN, "on": False, "peers": [], "ended": "alone"} in team["pushed"]
    assert team["pushed"][-1] == {"type": "live_team", "account": account, "space": SPACE, "on": False,
                                  "ended": "alone"}
    assert repo.link_get(account, PLUGIN) == SPACE  # still on the team copy: sync goes on


def test_a_groupmate_gone_from_the_roster_gets_a_grace_and_a_fresh_group_stays(team: dict[str, Any]) -> None:
    account = team["account"]
    _roster(account)
    live.join(PLUGIN)  # nobody else live: this PC hosts, alone
    assert live.end_lone_groups(account, SPACE) == []  # a group nobody joined yet stays
    now = time.time()
    _roster(account, _on("d_cy", "Cy", live.host_room(), now))
    assert live.end_lone_groups(account, SPACE, now=now) == []
    _roster(account)  # Cy vanished (a crash or a restart): maybe back in a moment
    assert live.end_lone_groups(account, SPACE, now=now + 30) == []
    assert live.end_lone_groups(account, SPACE, now=now + live.LONE_GRACE_S + 1) == [PLUGIN]


def _second_plugin(account: str) -> None:
    repo.link_set(account, OTHER, SPACE)
    space_vcs.head_put(account, SPACE, OTHER, engine="v2")


def test_one_live_covers_every_plugin_of_the_team_copy(team: dict[str, Any]) -> None:
    """Start live from one plugin puts every plugin of the team copy here in the same group
    with one since (each works out the same leader); Leave, from either, takes them all out."""
    account, impl = team["account"], team["impl"]
    _second_plugin(account)
    try:
        got = live.join(PLUGIN)
        me = live.host_room()
        assert got["ok"] and sorted(impl.joined) == [(SPACE, PLUGIN, me), (SPACE, OTHER, me)]
        assert live.room_of(PLUGIN, account, SPACE)["since"] == live.room_of(OTHER, account, SPACE)["since"]
        blob = vcs_engine.presence_blob(account, SPACE, [PLUGIN, OTHER])
        assert blob["live"]["room"] == me and blob["live"]["v"] == live.PROTOCOL  # at its top, and per plugin
        assert blob["plugins"][OTHER]["live"]["room"] == me
        live.leave_live(OTHER)
        assert not live.is_live(PLUGIN) and not live.is_live(OTHER) and live.spaces_live() == set()
        assert "live" not in vcs_engine.presence_blob(account, SPACE, [PLUGIN, OTHER])
    finally:
        repo.link_set(account, OTHER, scopes.PERSONAL)


def test_a_plugin_that_comes_to_the_team_copy_later_joins_the_group(team: dict[str, Any],
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled later: the next roster brings it in. Switched to Local: it alone leaves. Back
    on the team copy: it joins as soon as it is read."""
    account = team["account"]
    on = {OTHER: False}
    monkeypatch.setattr(live, "_enabled", lambda plugin: on.get(plugin, True))
    _second_plugin(account)
    try:
        live.join(PLUGIN)
        assert not live.is_live(OTHER)  # not enabled here
        on[OTHER] = True
        live.catch_up(account, SPACE)  # the next roster
        deadline = time.monotonic() + 5
        while not live.is_live(OTHER) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert live.room_of(OTHER, account, SPACE)["room"] == live.host_room()
        repo.link_set(account, OTHER, scopes.PERSONAL)
        assert live.status(OTHER)["on"] is False and live.is_live(PLUGIN)
        repo.link_set(account, OTHER, SPACE)
        assert live.status(OTHER)["on"] is True
    finally:
        repo.link_set(account, OTHER, scopes.PERSONAL)


def test_closing_a_plugins_tab_keeps_this_pc_live(team: dict[str, Any]) -> None:
    """Live is the team copy's, not a tab's: every tab of the plugin closing ends nothing."""
    from frontend.ui_web import panel_api  # noqa: F401 — the window mixin imports through it
    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    live.join(PLUGIN)
    tab = f"plugin:{PLUGIN}:main"
    PanelApiWindowMixin.report_plugin_panel(None, "main", tab, visible=True, open=True)  # type: ignore[arg-type]
    PanelApiWindowMixin.report_plugin_panel(None, "main", tab, visible=False, open=False)  # type: ignore[arg-type]
    time.sleep(0.2)
    assert live.is_live(PLUGIN) and live.team_status(team["account"], SPACE)["on"]


def test_the_menus_groups_cover_every_plugin_and_say_what_each_person_has_on_screen(team: dict[str, Any]) -> None:
    account = team["account"]
    cy = {**_on("d_cy", "Cy", "h:d_cy", 10, plugin=OTHER), "live": {"room": "h:d_cy", "since": 10, "v": live.PROTOCOL}}
    _roster(account, cy, _on("d_mo", "Mo", "h:d_cy", 20))  # Mo's app says it per plugin only
    (g,) = live.team_groups(account, SPACE)
    assert [(m["name"], m["plugin"], m["partial"]) for m in g["members"]] == [("Cy", OTHER, False),
                                                                             ("Mo", PLUGIN, True)]
    assert live.join(PLUGIN)["room"] == "h:d_cy"  # Start live: their group, whatever plugin they're in
    got = live.overview()
    (t,) = got["teams"]
    assert got["on"] and (t["space"], t["label"], t["on"], t["room"]) == (SPACE, "Alpha", True, "h:d_cy")
    assert t["plugins"] == [PLUGIN] and [m["name"] for m in t["groups"][0]["members"]] == ["Cy", "Mo", "You"]


def test_the_overview_lists_a_saved_team_copy_before_any_plugin_or_the_provider_is_loaded(
        team: dict[str, Any]) -> None:
    """App start: no plugin panel opened yet, so the Live menu has only the saved links."""
    got = live.overview()
    (t,) = got["teams"]
    assert (t["space"], t["label"], t["supported"], got["on"]) == (SPACE, "Alpha", True, False)
    spaces.reset_for_tests()  # the account plugin (the provider) hasn't loaded either
    (t,) = live.overview()["teams"]
    assert t["space"] == SPACE and not t["supported"] and t["reason"]


def test_a_remembered_group_is_gone_back_into_after_a_restart_while_it_is_live(team: dict[str, Any]) -> None:
    account = team["account"]
    live.remember_team(account, SPACE, "h:d_iliya")
    _roster(account)
    assert live.status(PLUGIN)["rejoin"] == ""  # their group ended
    live.catch_up(account, SPACE)
    time.sleep(0.1)
    assert not live.is_live(PLUGIN)
    _roster(account, _on("d_iliya", "iliya", "h:d_iliya", 10))
    assert live.status(PLUGIN)["rejoin"] == "h:d_iliya"
    live.catch_up(account, SPACE)  # the first roster after a restart: back in
    _wait_room("h:d_iliya")
    assert live.status(PLUGIN)["room"] == "h:d_iliya"
    live.leave_live(PLUGIN)  # Leave: not again
    assert live.status(PLUGIN)["rejoin"] == ""
    live.remember_team(account, SPACE, live.START)  # Start live before the restart
    _roster(account)
    live.catch_up(account, SPACE)
    _wait_room(live.host_room())
    assert live.status(PLUGIN)["room"] == live.host_room()


def test_a_space_is_live_while_this_pc_is_live_on_it(team: dict[str, Any]) -> None:
    assert live.spaces_live() == set()
    live.join(PLUGIN)
    assert live.spaces_live() == {(team["account"], SPACE)}
    live.leave_live(PLUGIN)
    assert live.spaces_live() == set()


def test_presence_goes_one_beat_at_a_time_and_again_when_the_room_moved_meanwhile(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Join leaves one room and goes into another: two beats at once could land the older
    last, so the team would see this PC where it no longer is."""
    gate, beats = threading.Event(), []

    def beat(account: str, space: str, backend: Any, plugins: list[str], *, force: bool = False) -> dict[str, Any]:
        beats.append(force)
        if len(beats) == 1:
            gate.wait(5)
        return {"ok": True}

    monkeypatch.setattr(repo, "links", lambda account: {PLUGIN: SPACE})
    monkeypatch.setattr(spaces, "ledgers", lambda account, space, plugins: {PLUGIN: object()})
    monkeypatch.setattr(vcs_engine, "beat", beat)
    live.reset_for_tests()

    def settled(n: int) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (len(beats) < n or live._beating):
            time.sleep(0.01)
        return len(beats) == n and not live._beating

    live._beat_soon("acct", SPACE)
    while not beats:
        time.sleep(0.01)
    live._beat_soon("acct", SPACE)  # the room moved while the first beat went
    live._beat_soon("acct", SPACE)
    assert beats == [True]  # one at a time
    gate.set()
    assert settled(2)  # one more, after it: it reads the room now
    live._beat_soon("acct", SPACE)
    assert settled(3) and beats == [True, True, True]  # nothing going: at once
    live.reset_for_tests()


def test_an_older_roster_coming_back_last_never_ends_a_group_still_together(team: dict[str, Any]) -> None:
    """A live beat and the scheduler's at once come back in either order: the roster of the
    call made first, landing last, is not kept (it would show Cy outside the group, and Live
    would end here as if Cy left)."""
    account = team["account"]
    t = time.time()
    live.join(PLUGIN, "h:d_cy")
    cy = _on("d_cy", "Cy", "h:d_cy", t - 60)
    try:
        kept = vcs_engine._keep_roster(account, SPACE, t + 2, [cy], now=t)
        assert kept is not None
        vcs_engine._roster_came(account, SPACE, *kept)
        gone = {**cy, "plugins": {PLUGIN: {"viewing": True}}}  # Cy before going live, asked for earlier
        assert vcs_engine._keep_roster(account, SPACE, t + 1, [gone], sent_sha="s1", now=t) is None
        got = space_vcs.presence_get(account, SPACE)
        assert got["roster"]["people"][0]["plugins"][PLUGIN]["live"]["room"] == "h:d_cy" and got["sent_sha"] == "s1"
        live.end_lone_groups(account, SPACE)
        assert live.status(PLUGIN)["on"] is True
    finally:
        vcs_engine._roster_asked.pop((account, SPACE), None)


def test_a_commit_note_goes_at_most_once_a_second_while_a_round_sends_and_always_at_its_end(
        team: dict[str, Any]) -> None:
    live.join(PLUGIN)
    impl = team["impl"]
    notes = lambda: [s for s in impl.sent if s[1] == live.CHANGE]  # noqa: E731
    live.committed(team["account"], SPACE, PLUGIN, throttle=True)
    live.committed(team["account"], SPACE, PLUGIN, throttle=True)  # the next changeset, a moment later
    assert len(notes()) == 1
    live.committed(team["account"], SPACE, PLUGIN)  # the round is done
    assert len(notes()) == 2


def test_a_plugin_reloading_stays_live_and_one_disabled_leaves(team: dict[str, Any],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A reload or an update drops the plugin's runtime and loads it again at once: its group
    never sees it leave (a teammate left alone would end their Live). A disable does leave."""
    from backend.uefn_plugins import host

    live.join(PLUGIN)
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: True)
    host.invalidate_plugin_runtime(PLUGIN)
    assert live.is_live(PLUGIN) and team["impl"].joined[-1] == (SPACE, PLUGIN, live.host_room())
    monkeypatch.setattr(host, "is_plugin_enabled", lambda pid: False)
    host.invalidate_plugin_runtime(PLUGIN)
    assert not live.is_live(PLUGIN) and team["impl"].joined[-1] == (SPACE, PLUGIN, None)


def test_switching_to_local_ends_live(team: dict[str, Any]) -> None:
    live.join(PLUGIN)
    repo.link_set(team["account"], PLUGIN, scopes.PERSONAL)
    assert live.status(PLUGIN)["on"] is False
    assert team["impl"].joined[-1] == (SPACE, PLUGIN, None)


def test_the_bar_shows_who_is_live_and_how_they_are_reached(team: dict[str, Any]) -> None:
    team["impl"].peers = [{**ALEX, "path": "direct"}, {"name": "Sam", "device": "d_sam", "path": "none"}]
    live.join(PLUGIN)
    st = live.status(PLUGIN)
    assert [p["path"] for p in st["peers"]] == ["direct", "none"]


def test_a_group_member_with_no_link_is_named_after_a_grace(team: dict[str, Any]) -> None:
    now = time.time()
    _roster(team["account"], _on("d_iliya", "iliya", "h:d_iliya", now - 60), _on("d_cy", "Cy", "h:d_iliya", now))
    assert live.join(PLUGIN, "h:d_iliya")["ok"]
    st = live.status(PLUGIN)
    assert st["unlinked"] == []  # this PC just joined: still connecting to everyone
    assert sorted(st["connecting"]) == ["Cy", "iliya"]
    with live._lock:
        live._live[PLUGIN].since = now - 60
    team["impl"].peers = [{**ALEX, "device": "d_iliya", "path": "direct"}]
    st = live.status(PLUGIN)
    assert st["unlinked"] == [] and st["connecting"] == ["Cy"]  # Cy only just went live
    team["impl"].peers = [{**ALEX, "device": "d_iliya", "path": "none"}]
    assert live.status(PLUGIN)["unlinked"] == ["iliya"]  # presence has them, nothing reaches them


def test_this_account_on_another_pc_is_you_on_that_pc(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    account = team["account"]
    monkeypatch.setattr(vcs_engine, "my_user_id", lambda: "u_me")
    laptop = {**_on("d_lap", "me", "h:d_lap", 10), "user": "u_me", "label": "Laptop"}
    space_vcs.presence_put(account, SPACE, {"people": [laptop], "devices": {"d_lap": "Laptop"}}, sent_sha="", now=time.time())
    assert live.status(PLUGIN)["groups"][0]["members"][0]["name"] == "You on Laptop"
    live.join(PLUGIN, "h:d_lap")
    team["pushed"].clear()
    live.deliver(account, SPACE, PLUGIN, {"name": "me", "user": "u_me", "device": "d_lap"}, "ducky.pointer", {})
    assert team["pushed"][-1]["from"]["name"] == "You on Laptop"  # pointers say which PC


# --------------------------------------------------------------------------- what goes out


def test_a_plugin_sends_its_own_topics_within_the_limits(team: dict[str, Any]) -> None:
    impl = team["impl"]
    assert live.send(PLUGIN, "cursor", {"x": 1})["ok"] is False  # not live yet
    live.join(PLUGIN)
    assert live.send(PLUGIN, "ducky.change", {})["ok"] is False  # the host's, after a commit only
    assert live.send(PLUGIN, "ducky.secret", {})["ok"] is False
    assert live.send(PLUGIN, "Bad Topic", {})["ok"] is False
    assert live.send(PLUGIN, "chat", {"t": "x" * (live.LIMIT_BYTES + 1)})["ok"] is False
    assert live.send(PLUGIN, "chat", object())["ok"] is False  # not JSON
    impl.sent.clear()
    assert live.send(PLUGIN, "chat", {"t": "hi"}, to="d_alex") == {"ok": True, "sent": 1}
    assert live.send(PLUGIN, "ducky.pointer", {"anchor": "body"})["ok"] is True
    assert live.send(PLUGIN, "ducky.hold", {"anchor": "body", "on": True, "t": 1})["ok"] is True
    # Pointers may drop (the next replaces them); a hold, and its let-go, never may.
    assert impl.sent == [(PLUGIN, "chat", {"t": "hi"}, "d_alex", False), (PLUGIN, "ducky.pointer", {"anchor": "body"}, "", True),
                         (PLUGIN, "ducky.hold", {"anchor": "body", "on": True, "t": 1}, "", False)]
    results = [live.send(PLUGIN, "chat", {"n": n})["ok"] for n in range(live.LIMIT_PER_S + 5)]
    assert results.count(False) >= 5  # at most 30 a second


def test_a_write_never_goes_live_a_commit_tells_the_group_to_pull(team: dict[str, Any]) -> None:
    impl, account, data = team["impl"], team["account"], PluginData(PLUGIN)
    live.join(PLUGIN)
    impl.sent.clear()
    data.put("card.pip", {"hp": 2})
    data.put_file("art/pip.png", b"\x89PNG")
    data.delete("card.pip")
    assert impl.sent == []  # half a change (a doc without its picture) never reaches anyone
    live.committed(account, SPACE, PLUGIN)
    assert impl.sent == [(PLUGIN, live.CHANGE, {}, "", False)]
    live.leave_live(PLUGIN)
    live.committed(account, SPACE, PLUGIN)  # not live: the group's next round brings it
    assert len(impl.sent) == 1


def test_a_teammates_commit_pulls_it_now_and_nothing_shows_before_it_lands(team: dict[str, Any]) -> None:
    account, data = team["account"], PluginData(PLUGIN)
    data.put("card.pip", {"hp": 1})
    live.deliver(account, SPACE, PLUGIN, ALEX, live.CHANGE, {})
    assert team["woke"] == []  # not live here: nothing comes in live
    live.join(PLUGIN)
    team["woke"].clear()
    team["pushed"].clear()
    live.deliver(account, SPACE, PLUGIN, ALEX, live.CHANGE, {"kind": "doc", "key": "card.pip", "value": "{}"})
    assert team["woke"] == [(account, SPACE, PLUGIN)]
    assert data.get("card.pip") == {"hp": 1}  # what is stored, until the pull lands it whole
    assert team["pushed"] == []  # the pull tells the panel what it changed


def test_a_plugins_own_topics_reach_its_panels_and_backend(team: dict[str, Any]) -> None:
    account = team["account"]
    heard: list[tuple[dict[str, str], Any]] = []
    api = live.LiveApi(PLUGIN)
    api.on("cursor", lambda who, data: heard.append((who, data)))
    api.on("cursor", lambda who, data: 1 / 0)  # a broken callback never stops delivery
    with pytest.raises(TypeError):
        api.on("cursor", "not a function")  # type: ignore[arg-type]
    api.join()
    team["pushed"].clear()
    live.deliver(account, SPACE, PLUGIN, ALEX, "cursor", {"x": 3})
    assert heard == [(ALEX, {"x": 3})]
    assert team["pushed"] == [{"type": "live_message", "plugin": PLUGIN, "from": ALEX, "topic": "cursor", "data": {"x": 3}}]
    live.forget(PLUGIN)
    live.deliver(account, SPACE, PLUGIN, ALEX, "cursor", {"x": 4})
    assert len(heard) == 1


# --------------------------------------------------------------------------- holds


def test_a_hold_goes_to_the_group_at_once_and_to_everyone_in_presence(team: dict[str, Any]) -> None:
    impl, account = team["impl"], team["account"]
    assert live.hold(PLUGIN, '[id="title"]', True, 5, "vabc") == {"ok": True}
    assert impl.sent == []  # not live: presence only
    blob = vcs_engine.presence_blob(account, SPACE, [PLUGIN])
    assert blob["plugins"][PLUGIN]["holds"] == [{"a": '[id="title"]', "t": 5.0, "v": "vabc"}]
    live.join(PLUGIN)
    impl.sent.clear()
    live.hold(PLUGIN, "body>button:nth-of-type(1)", True, 7, "vabc")
    assert impl.sent == [(PLUGIN, live.HOLD, {"anchor": "body>button:nth-of-type(1)", "on": True, "t": 7.0, "v": "vabc"}, "", False)]
    live.hold(PLUGIN, '[id="title"]', False)
    assert [h["a"] for h in live.holds_of(PLUGIN)] == ["body>button:nth-of-type(1)"]
    # A hold the page stopped renewing goes; at most a few go in presence; bad ones never.
    assert live.holds_of(PLUGIN, now=time.time() + live.HOLD_STALE_S + 1) == []
    for i in range(live.HOLDS_MAX + 3):
        live.hold(PLUGIN, f"body>div:nth-of-type({i + 1})", True, i, "v1")
    assert len(live.holds_of(PLUGIN)) == live.HOLDS_MAX
    assert live.hold(PLUGIN, "x" * 401, True)["ok"] is False
    assert live.hold(PLUGIN, "body", True, 1, "bad view!")["ok"] is False
    repo.link_set(account, PLUGIN, scopes.PERSONAL)
    assert live.hold(PLUGIN, "body", True)["ok"] is False  # Local: nobody to tell


def test_teammates_holds_from_their_presence_reach_the_panels_when_they_change(team: dict[str, Any]) -> None:
    account = team["account"]
    me = space_vcs.device_id()
    mo = {"device": "d_mo", "user": "u_mo", "user_name": "Mo",
          "plugins": {PLUGIN: {"viewing": True, "holds": [{"a": '[id="title"]', "t": 3, "v": "vx"}]}}}
    mine = {"device": me, "user": "u_me", "user_name": "Me", "plugins": {PLUGIN: {"holds": [{"a": "body", "t": 1, "v": ""}]}}}
    live.roster_holds(account, SPACE, {"people": [mo, mine]})
    told = [e for e in team["pushed"] if e["type"] == "live_holds"]
    assert told == [{"type": "live_holds", "plugin": PLUGIN,
                     "holds": [{"device": "d_mo", "name": "Mo", "anchor": '[id="title"]', "t": 3, "v": "vx"}]}]
    assert live.holds_now(PLUGIN) == told[0]["holds"]
    live.roster_holds(account, SPACE, {"people": [mo, mine]})  # nothing moved: nothing told
    assert len([e for e in team["pushed"] if e["type"] == "live_holds"]) == 1
    live.roster_holds(account, SPACE, {"people": [mine]})  # Mo let go
    assert [e for e in team["pushed"] if e["type"] == "live_holds"][-1] == {"type": "live_holds", "plugin": PLUGIN, "holds": []}


def test_a_roster_from_heads_or_a_beat_tells_the_panels_what_teammates_hold(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(live, "roster_holds", lambda a, s, r: got.append((a, s, r)))
    vcs_engine._roster_came(team["account"], SPACE, {}, {"people": []})
    assert got == [(team["account"], SPACE, {"people": []})]


# --------------------------------------------------------------------------- folder plugins


def test_a_live_folder_plugins_pages_show_a_teammates_pull_at_once_unless_this_pc_is_editing_in_them(
        team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A teammate's pull restarts a folder plugin. Its pages reload and show it (they don't
    merge files themselves), except while this PC presses or types in one of them: then they
    stay as they are and reload as soon as it lets go, so an edit in progress isn't lost."""
    from backend.store.repos import folder_files
    from backend.uefn_plugins import host
    from frontend.ui_web import agent_modes

    told: list[dict[str, Any]] = []
    restarted: list[str] = []
    monkeypatch.setattr(agent_modes, "push_ui_event", told.append)
    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: restarted.append(pid))
    live.join(PLUGIN)
    folder_files.reload_bump(PLUGIN, "pull")
    host._restart_moved(PLUGIN)  # nothing pressed here: the pages reload
    assert restarted == [PLUGIN]
    assert told == [{"type": "plugin_scope_changed", "plugins": [PLUGIN], "switched": True}]
    told.clear()
    assert live.hold(PLUGIN, "#name", True)["ok"]  # typing in a field
    folder_files.reload_bump(PLUGIN, "pull")
    host._restart_moved(PLUGIN)
    assert told == [{"type": "plugin_scope_changed", "plugins": [PLUGIN], "synced": True, "live": True}]
    before = folder_files.reload_epoch(PLUGIN)
    live.hold(PLUGIN, "#name", False)  # let go: the reload it waited with happens now
    assert folder_files.reload_epoch(PLUGIN) == before + 1 and folder_files.reload_reason(PLUGIN) == "live ended"
    live.leave_live(PLUGIN)
    assert folder_files.reload_epoch(PLUGIN) == before + 1  # nothing left waiting


def test_joining_a_teammates_group_reloads_the_panels_on_its_first_pull(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store.repos import folder_files
    from backend.uefn_plugins import host
    from frontend.ui_web import agent_modes

    told: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_modes, "push_ui_event", told.append)
    monkeypatch.setattr(host, "reload_single_plugin", lambda pid: None)
    live.join(PLUGIN, "h:d_alex")
    folder_files.reload_bump(PLUGIN, "pull")
    host._restart_moved(PLUGIN)  # the join's pull: the page opens on the host's data
    assert told == [{"type": "plugin_scope_changed", "plugins": [PLUGIN], "switched": True}]
    told.clear()
    live.hold(PLUGIN, "#name", True)  # later pulls while this PC types in a page: kept as it is
    folder_files.reload_bump(PLUGIN, "pull")
    host._restart_moved(PLUGIN)
    assert told == [{"type": "plugin_scope_changed", "plugins": [PLUGIN], "synced": True, "live": True}]


def test_live_folder_changes_never_wait_behind_update_now(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from backend.uefn_plugins import panel_presence
    from backend.uefn_plugins import team_folders as tf

    monkeypatch.setattr(tf, "plugin_folders", lambda plugin: [SimpleNamespace(after_pull="reload")])
    monkeypatch.setattr(panel_presence, "panel_state", lambda plugin: {"visible": True})
    ctx = SimpleNamespace(plugin=PLUGIN)
    assert vcs_engine._holds_incoming(ctx) is True  # on screen: they wait for Update now
    live.join(PLUGIN)
    assert vcs_engine._holds_incoming(ctx) is False  # live: they land and the panels merge them


# --------------------------------------------------------------------------- live data ("data_live")


@pytest.fixture
def live_data(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``cards`` shares its data live; the outbox runs only when the test asks."""
    monkeypatch.setattr(scopes, "data_live", lambda plugin: plugin == PLUGIN)
    monkeypatch.setattr(live, "_outbox_start", lambda: None)
    return team


def _data_sent(team: dict[str, Any]) -> list[dict[str, Any]]:
    return [data for plugin, topic, data, _to, _fast in team["impl"].sent if topic == live.DATA]


def _item(key: str, value: Any, at: float) -> dict[str, Any]:
    raw = scopes.encode_doc(value)
    return {"k": "doc", "key": key, "op": "put", "at": at, "sha": hashlib.sha256(raw).hexdigest(),
            "v": raw.decode("utf-8")}


def test_a_live_plugins_writes_go_to_the_group_at_once(live_data: dict[str, Any]) -> None:
    data = PluginData(PLUGIN)
    data.put("card.old", {"hp": 0})  # before going live: that goes with sync only
    live.join(PLUGIN)
    data.put("card.pip", {"hp": 1})
    assert live.send_changes() == 1
    sent = _data_sent(live_data)
    assert [(d["k"], d["key"], d["op"]) for d in sent] == [("doc", "card.pip", "put")]
    assert json.loads(sent[0]["v"]) == {"hp": 1} and sent[0]["sha"] == _row(live_data["account"], "card.pip")["sha256"]
    assert live.send_changes() == 0  # each write goes once
    data.delete("card.pip")
    data.put_file("art/pip.png", b"PNG")
    assert live.send_changes() == 2
    gone, art = _data_sent(live_data)[1:]
    assert (gone["key"], gone["op"]) == ("card.pip", "del")
    assert (art["k"], art["key"], base64.b64decode(art["b"])) == ("asset", "art/pip.png", b"PNG")
    assert _row(live_data["account"], "card.old")["dirty"] == 1  # still on its way with sync


def test_a_groupmates_change_lands_at_once_as_a_pulled_copy(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    heard: list[list[str]] = []
    scopes.add_change_listener(PLUGIN, heard.append)
    live.join(PLUGIN, "h:d_alex")
    live.deliver(account, SPACE, PLUGIN, ALEX, live.DATA, _item("card.pip", {"hp": 7}, time.time()))
    assert PluginData(PLUGIN).get("card.pip") == {"hp": 7}
    assert _row(account, "card.pip")["dirty"] == 0  # theirs: sync records it from their PC, not this one
    assert heard == [["card.pip"]]  # the plugin's backend hears it like a pull
    assert {"type": "plugin_scope_changed", "plugins": [PLUGIN], "synced": True,
            "keys": {PLUGIN: ["card.pip"]}} in live_data["pushed"]
    assert live.send_changes() == 0  # never sent back to the group
    assert not [p for p in live_data["pushed"] if p.get("type") == "live_message"]  # not a page message
    live.deliver(account, SPACE, PLUGIN, ALEX, live.DATA, {"k": "doc", "key": "card.pip", "op": "del", "at": time.time()})
    assert PluginData(PLUGIN).get("card.pip") is None


def test_the_newest_change_of_an_item_wins_inside_the_group(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    live.join(PLUGIN, "h:d_alex")
    then = time.time() - 60
    PluginData(PLUGIN).put("card.pip", {"hp": 1})  # mine, written after their change was made
    assert not live._apply_data(account, SPACE, PLUGIN, ALEX, _item("card.pip", {"hp": 2}, then))
    assert PluginData(PLUGIN).get("card.pip") == {"hp": 1} and _row(account, "card.pip")["dirty"] == 1
    assert live._apply_data(account, SPACE, PLUGIN, ALEX, _item("card.pip", {"hp": 3}, time.time() + 1))
    assert PluginData(PLUGIN).get("card.pip") == {"hp": 3} and _row(account, "card.pip")["dirty"] == 0


def test_a_change_too_big_to_carry_is_pulled_instead(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    live.join(PLUGIN, "h:d_alex")
    live_data["woke"].clear()
    item = _item("card.big", {"x": 1}, time.time())
    del item["v"]
    assert not live._apply_data(account, SPACE, PLUGIN, ALEX, item)
    assert (account, SPACE, PLUGIN) in live_data["woke"]
    bad = _item("card.pip", {"hp": 1}, time.time())
    bad["sha"] = "0" * 64  # damaged: never lands
    assert not live._apply_data(account, SPACE, PLUGIN, ALEX, bad) and PluginData(PLUGIN).get("card.pip") is None


def test_members_of_a_live_group_never_lock_each_other_out(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    unit = "doc:card.pip"
    space_vcs.lock_put(account, SPACE, PLUGIN, unit, name="Pip", holder_user="u_alex", holder_name="Alex",
                       holder_device="d_alex", lease_left=60)
    with pytest.raises(scopes.LockedOut):  # not live: Alex's lock holds as before
        PluginData(PLUGIN).put("card.pip", {"hp": 1})
    live.join(PLUGIN, "h:d_alex")
    _roster(account, _on("d_alex", "Alex", "h:d_alex", 1.0))
    assert live.group_devices(PLUGIN, account, SPACE) == {"d_alex"}
    assert PluginData(PLUGIN).put("card.pip", {"hp": 1})["changed"]  # Alex is in the group
    PluginData(PLUGIN).put("card.zed", {"hp": 2})
    assert space_vcs.lock_request_get(account, SPACE, PLUGIN, "doc:card.zed") is None  # no auto lock asked for
    space_vcs.lock_put(account, SPACE, PLUGIN, "doc:card.ben", name="Ben", holder_user="u_ben", holder_name="Ben",
                       holder_device="d_ben", lease_left=60)
    with pytest.raises(scopes.LockedOut):  # Ben isn't in the group: his lock still holds
        PluginData(PLUGIN).put("card.ben", {"hp": 3})


CY = {"name": "Cy", "user": "u_cy", "device": "d_cy", "label": "CY-PC"}


def _ctx(account: str) -> Any:
    return vcs_engine.Ctx(account, SPACE, PLUGIN, None)


def test_the_leader_takes_a_groupmates_change_on_and_names_who_made_it(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    _roster(account)
    live.join(PLUGIN)  # nobody else live: this PC hosts, and leads
    _roster(account, _on("d_cy", "Cy", live.host_room(), time.time()))
    item = {**_item("card.pip", {"hp": 3}, time.time()), "by": {"actor": "agent", "ducky": "Builder"}}
    live.deliver(account, SPACE, PLUGIN, CY, live.DATA, item)
    assert PluginData(PLUGIN).get("card.pip") == {"hp": 3}
    assert _row(account, "card.pip")["dirty"] == 1  # the leader's to send, for Cy
    assert vcs_engine._build(_ctx(account), now=time.time()) == 1
    (cs,) = space_vcs.cs_open(account, SPACE, PLUGIN)
    assert (cs["actor"], cs["ducky"], cs["author"], cs["author_pc"]) == ("agent", "Builder", "Cy", "CY-PC")
    live_data["impl"].sent.clear()
    assert live.send_changes() == 0  # never sent back to the group it came from
    # Someone the roster doesn't show in the group yet sends their own.
    live.deliver(account, SPACE, PLUGIN, ALEX, live.DATA, _item("card.zed", {"hp": 1}, time.time()))
    assert _row(account, "card.zed")["dirty"] == 0


def test_a_groupmate_holds_its_changes_for_the_leader_while_it_is_in_reach(live_data: dict[str, Any]) -> None:
    account, impl = live_data["account"], live_data["impl"]
    _roster(account, _on("d_cy", "Cy", "h:d_cy", time.time() - 60))
    live.join(PLUGIN, "h:d_cy")
    impl.peers = [{**CY, "path": "direct"}]
    PluginData(PLUGIN).put("card.pip", {"hp": 1})
    now = time.time()
    assert vcs_engine._build(_ctx(account), now=now) == 0  # Cy leads: Cy sends it
    assert vcs_engine._build(_ctx(account), now=now + live.HOLD_MAX_S + 1) == 1  # not sent for us in time: ours
    PluginData(PLUGIN).put("card.zed", {"hp": 2})
    impl.peers = [{**CY, "path": "none"}]
    assert vcs_engine._build(_ctx(account), now=time.time()) == 1  # the leader is out of reach: ours at once
    impl.peers = [{**CY, "path": "direct"}]
    PluginData(PLUGIN).put("card.tiny", {"hp": 3})
    PluginData(PLUGIN).put_file("art/clip.webm", b"x" * live.DATA_INLINE_MAX)  # too big to go live
    assert vcs_engine._build(_ctx(account), now=time.time()) == 1  # Cy can't send what never reached it: ours
    going = space_vcs.cs_items_open(account, SPACE, PLUGIN)
    assert ("asset", "art/clip.webm") in going and ("doc", "card.tiny") not in going  # the small one waits for Cy


def test_a_member_holds_a_change_only_for_a_leader_whose_app_sends_this_plugins_data(
        live_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Every plugin's data goes live from live version 3 (DATA_V). A leader on version 2
    (1.2.435-1.2.438) sends only the data of plugins that ask for it in their manifest: for
    any other, a member that held its change would wait 90 s for nothing."""
    account, impl = live_data["account"], live_data["impl"]
    since = time.time() - 60
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since, app="1.2.438"))
    live.join(PLUGIN, "h:d_cy")
    impl.peers = [{**CY, "path": "direct"}]
    monkeypatch.setattr(scopes, "data_live_asked", lambda plugin: False)
    assert live.hold_since(account, SPACE, PLUGIN) is None  # this PC sends its own
    monkeypatch.setattr(scopes, "data_live_asked", lambda plugin: True)  # "data_live": true in the manifest
    assert live.hold_since(account, SPACE, PLUGIN) is not None
    monkeypatch.setattr(scopes, "data_live_asked", lambda plugin: False)
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since))  # Cy updated: live version 3
    assert live.hold_since(account, SPACE, PLUGIN) is not None


def test_every_shared_plugins_data_goes_live_unless_its_manifest_says_no() -> None:
    from backend.uefn_plugins import store

    assert store.data_live({}) and store.data_live({"data_live": True})
    assert not store.data_live({"data_live": False}) and not store.data_live({"data_sharing": False})


def test_the_new_leader_gets_what_the_old_one_didnt_send(live_data: dict[str, Any]) -> None:
    account = live_data["account"]
    since = time.time() - 60
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since), _on("d_mo", "Mo", "h:d_cy", since + 1))
    live.join(PLUGIN, "h:d_cy")
    PluginData(PLUGIN).put("card.pip", {"hp": 1})
    assert live.send_changes() == 1
    sent = _data_sent(live_data)[0]
    assert sent["by"] == {"actor": "user", "ducky": ""}  # who made it here
    assert live.own_echo(account, SPACE, PLUGIN, "doc", "card.pip", sent["sha"])
    assert live.send_changes() == 0
    _roster(account, _on("d_mo", "Mo", "h:d_cy", since + 1))  # Cy left: Mo leads now
    assert live.send_changes() == 1  # still unsent here: it goes again, for Mo to send
    assert live.send_changes() == 0


def test_a_pc_on_an_older_app_never_leads_a_group_and_sends_its_own_changes(live_data: dict[str, Any]) -> None:
    """Presence says each PC's live version, and the team service its app's version. An app
    from before groups ran through a leader relays nothing: the group runs through whoever up
    to date has been in it longest, and the older PC's changes are its own to send."""
    account, impl = live_data["account"], live_data["impl"]
    since = time.time() - 60
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since, app="1.2.433"))  # Cy's room, Cy's app is older
    live.join(PLUGIN, "h:d_cy")
    assert live.room_of(PLUGIN, account, SPACE)["v"] == live.PROTOCOL  # what this PC's presence says
    (g,) = live.status(PLUGIN)["groups"]
    assert g["hostIsMe"] and [(m["name"], m["older"]) for m in g["members"]] == [("Cy", True), ("You", False)]
    impl.peers = [{**CY, "path": "direct"}]
    PluginData(PLUGIN).put("card.pip", {"hp": 1})
    assert vcs_engine._build(_ctx(account), now=time.time()) == 1  # nobody to hold it for: this PC sends it
    live.deliver(account, SPACE, PLUGIN, CY, live.DATA, _item("card.zed", {"hp": 2}, time.time()))
    assert PluginData(PLUGIN).get("card.zed") == {"hp": 2}
    assert _row(account, "card.zed")["dirty"] == 0  # Cy's app sends its own
    # Mo, up to date and in the group before this PC, leads it; the older room's PC never does.
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since, app="1.2.433"), _on("d_mo", "Mo", "h:d_cy", since + 1))
    (g,) = live.status(PLUGIN)["groups"]
    assert (g["host"], g["hostDevice"], g["hostIsMe"]) == ("Mo", "d_mo", False)
    # A group with nobody up to date in it is led by its room's PC, as before.
    _roster(account, _on("d_bo", "Bo", "h:d_bo", since + 5, app="1.2.433"), _on("d_al", "Al", "h:d_bo", since, app="1.2.433"))
    bo = next(g for g in live.status(PLUGIN)["groups"] if g["room"] == "h:d_bo")
    assert bo["hostDevice"] == "d_bo"


def test_a_pc_that_says_no_version_relays_like_1_2_434_until_what_it_sends_says_otherwise(
        live_data: dict[str, Any]) -> None:
    """1.2.434 runs its group through a leader but its presence says no version: its room is
    its to lead, and a groupmate's change is its to send. A tab click with no origin (only an
    app before the leader relay sends one) shows it is older after all."""
    account = live_data["account"]
    since = time.time() - 60
    _roster(account, _on("d_cy", "Cy", "h:d_cy", since, app="1.2.434"))
    live.join(PLUGIN, "h:d_cy")
    (g,) = live.status(PLUGIN)["groups"]
    assert (g["hostDevice"], g["hostIsMe"]) == ("d_cy", False) and not g["members"][0]["older"]
    live.deliver(account, SPACE, PLUGIN, CY, live.NAV, {"anchor": "#video", "v": "v1", "o": "cy:1"})
    assert not live.status(PLUGIN)["groups"][0]["members"][0]["older"]  # 1.2.434's clicks name their origin
    live_data["pushed"].clear()
    live.deliver(account, SPACE, PLUGIN, CY, live.NAV, {"anchor": "#video"})
    (g,) = live.status(PLUGIN)["groups"]
    assert g["members"][0]["older"] and g["hostIsMe"]  # 1.2.433's never do: this PC leads now
    assert [e["type"] for e in live_data["pushed"]] == ["live_peers", "live_team", "live_message"]  # read again


def test_each_pc_has_a_live_state_everyone_in_the_group_sees(team: dict[str, Any]) -> None:
    account, impl, pushed = team["account"], team["impl"], team["pushed"]
    assert live.set_my_state(PLUGIN, {"cam": [0, 0, 5]})["ok"] is False  # not live
    live.join(PLUGIN)
    impl.sent.clear()
    assert live.set_my_state(PLUGIN, {"cam": [0, 0, 5]})["ok"]
    assert impl.sent == [(PLUGIN, live.STATE, {"s": {"cam": [0, 0, 5]}}, "", True)]  # may drop: the next replaces it
    assert live.set_my_state(PLUGIN, [1])["ok"] is False and live.set_my_state(PLUGIN, {"x": "y" * 5000})["ok"] is False
    assert live.send(PLUGIN, live.STATE, {})["ok"] is False  # the host's topic
    # A groupmate's: the panels and the backend hear it, a page that loads reads it.
    heard: list[tuple[str, Any]] = []
    live.LiveApi(PLUGIN).on_member_state(lambda who, state: heard.append((who["name"], state)))
    live.deliver(account, SPACE, PLUGIN, CY, live.STATE, {"s": {"cam": [1, 2, 3]}})
    live.deliver(account, SPACE, PLUGIN, CY, live.STATE, {"s": {"cam": [1, 2, 3]}})  # again for latecomers
    assert [p for p in pushed if p["type"] == "live_member_state"] == [
        {"type": "live_member_state", "plugin": PLUGIN, "from": CY, "state": {"cam": [1, 2, 3]}}]
    assert heard == [("Cy", {"cam": [1, 2, 3]})]
    assert live.member_states(PLUGIN) == [{"from": CY, "state": {"cam": [1, 2, 3]}}]
    # Mine goes again for latecomers; theirs goes once not heard of for a while.
    impl.sent.clear()
    live._state_tick(time.time() + live.STATE_EVERY_S + 1)
    assert impl.sent == [(PLUGIN, live.STATE, {"s": {"cam": [0, 0, 5]}}, "", True)]
    live._state_tick(time.time() + live.STATE_STALE_S + 1)
    assert live.member_states(PLUGIN) == [] and pushed[-1]["state"] is None
    live.set_my_state(PLUGIN, None)  # cleared: it must arrive
    assert impl.sent[-1] == (PLUGIN, live.STATE, {"s": None}, "", False)
    live.leave_live(PLUGIN)
    assert live.member_states(PLUGIN) == []


def test_a_members_shared_value_goes_through_the_leader_and_everyone_ends_on_its_order(team: dict[str, Any]) -> None:
    account, impl, pushed = team["account"], team["impl"], team["pushed"]
    me = space_vcs.device_id()
    assert live.share(PLUGIN, "player", {"playing": True})["ok"] is False  # not live
    _roster(account, _on("d_cy", "Cy", "h:d_cy", time.time() - 60))
    live.join(PLUGIN, "h:d_cy")  # Cy leads
    impl.sent.clear()
    assert live.share(PLUGIN, "player", {"playing": True, "t": 12})["ok"]
    assert impl.sent == [(PLUGIN, live.SHARE, {"k": "player", "v": {"playing": True, "t": 12}, "ask": 1}, "d_cy", False)]
    assert live.shared(PLUGIN) == {"player": {"value": {"playing": True, "t": 12}, "by": {"name": "You", "device": me, "me": True}}}
    # The leader's older value (a resend) while this one waits never puts the old one back.
    live.deliver(account, SPACE, PLUGIN, CY, live.SHARE, {"k": "player", "v": {"playing": False}, "ver": 0})
    assert live.shared(PLUGIN)["player"]["value"] == {"playing": True, "t": 12}
    # The leader's answers, in its order: this one, then Cy pausing it.
    live.deliver(account, SPACE, PLUGIN, CY, live.SHARE, {"k": "player", "v": {"playing": True, "t": 12}, "ver": 1,
                                                          "by": {"device": me}})
    live.deliver(account, SPACE, PLUGIN, CY, live.SHARE, {"k": "player", "v": {"playing": False, "t": 13}, "ver": 2})
    assert live.shared(PLUGIN)["player"] == {"value": {"playing": False, "t": 13}, "by": {"name": "Cy", "device": "d_cy"}}
    assert [p for p in pushed if p["type"] == "live_shared"][-1] == {
        "type": "live_shared", "plugin": PLUGIN, "key": "player", "value": {"playing": False, "t": 13},
        "by": {"name": "Cy", "device": "d_cy"}}
    # Only the leader's word counts, and an older version never wins.
    live.deliver(account, SPACE, PLUGIN, ALEX, live.SHARE, {"k": "player", "v": {"x": 1}, "ver": 9})
    live.deliver(account, SPACE, PLUGIN, CY, live.SHARE, {"k": "player", "v": {"old": 1}, "ver": 1})
    assert live.shared(PLUGIN)["player"]["value"] == {"playing": False, "t": 13}


def test_the_leader_orders_shared_values_and_sends_them_again_for_latecomers(team: dict[str, Any]) -> None:
    account, impl = team["account"], team["impl"]
    me = space_vcs.device_id()
    _roster(account)
    live.join(PLUGIN)  # this PC leads
    _roster(account, _on("d_cy", "Cy", live.host_room(), time.time()))
    impl.sent.clear()
    heard: list[tuple[str, Any, str]] = []
    live.LiveApi(PLUGIN).on_shared(lambda key, value, by: heard.append((key, value, str(by.get("name")))))
    live.deliver(account, SPACE, PLUGIN, CY, live.SHARE, {"k": "clip", "v": "c3", "ask": 1})
    assert impl.sent[-1] == (PLUGIN, live.SHARE, {"k": "clip", "v": "c3", "ver": 1, "by": {"name": "Cy", "device": "d_cy"}},
                             "", False)
    assert heard == [("clip", "c3", "Cy")]
    assert live.share(PLUGIN, "clip", "c4")["ok"]
    assert impl.sent[-1] == (PLUGIN, live.SHARE, {"k": "clip", "v": "c4", "ver": 2}, "", False)
    live.deliver(account, SPACE, PLUGIN, ALEX, live.SHARE, {"k": "clip", "v": "zz", "ask": 1})  # not in the group
    assert live.shared(PLUGIN)["clip"]["value"] == "c4"
    impl.sent.clear()
    live._state_tick(time.time() + live.STATE_EVERY_S + 1)
    assert (PLUGIN, live.SHARE, {"k": "clip", "v": "c4", "ver": 2, "by": {"device": me}}, "", True) in impl.sent
    assert live.share(PLUGIN, "bad key!", 1)["ok"] is False
    assert live.share(PLUGIN, "big", "y" * 9000)["ok"] is False
    assert live.share(PLUGIN, "clip", None)["ok"] and "clip" not in live.shared(PLUGIN)
    live.leave_live(PLUGIN)
    assert live.shared(PLUGIN) == {}


def test_history_names_who_made_a_change_the_leader_sent(team: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.uefn_plugins.ledger import History, HistoryEntry

    class Backend:
        def history(self, space: str, plugin: str, key: Any, before: Any, limit: int) -> History:
            return History((HistoryEntry(seq=5, by="iliya", device="d_il", device_name="Laptop", actor="agent",
                                         ducky="Builder", author="Cy", author_pc="CY-PC"),
                            HistoryEntry(seq=4, by="iliya", device="d_il", device_name="Laptop")))

    monkeypatch.setattr(spaces, "ledger_for", lambda account, space, plugin: Backend())
    sent, own = spaces.team_history(PLUGIN)["entries"]
    assert (sent["by"], sent["device"], sent["via"], sent["ducky"]) == ("Cy", "CY-PC", "iliya", "Builder")
    assert (own["by"], own["device"]) == ("iliya", "Laptop") and "via" not in own


def test_every_process_sees_where_the_app_is_live(live_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from backend.store.repos import kv

    account = live_data["account"]
    live.join(PLUGIN, "h:d_alex")
    assert live.room_anywhere(PLUGIN, account, SPACE) == "h:d_alex"
    doc = kv.get_doc("workspace_state", live.ROOM_KEY + PLUGIN)
    assert doc["room"] == "h:d_alex" and doc["pid"] == os.getpid()
    with live._lock:
        live._live.clear()  # another process: nothing live in it, the app's room is read
    kv.set_doc("workspace_state", live.ROOM_KEY + PLUGIN, {**doc, "pid": os.getppid()})  # a running app
    assert live.room_anywhere(PLUGIN, account, SPACE) == "h:d_alex"
    kv.set_doc("workspace_state", live.ROOM_KEY + PLUGIN, {**doc, "pid": 2 ** 31 - 7})  # an app that is gone
    assert live.room_anywhere(PLUGIN, account, SPACE) == ""
    assert live.group_devices(PLUGIN, account, SPACE) is None


def test_data_live_is_checked_in_the_manifest() -> None:
    from backend.uefn_plugins.store import data_live, manifest_data_errors

    assert data_live({"data_live": True}) and not data_live({"data_live": True, "data_sharing": False})
    assert any('"data_live"' in e for e in manifest_data_errors({"data_live": "yes"}))
    assert any('"data_live"' in e for e in manifest_data_errors({"data_live": True, "data_sharing": False}))
    assert not manifest_data_errors({"data_live": True, "data_sharing": True})


# --------------------------------------------------------------------------- the page


def test_panel_pages_get_the_pointer_script() -> None:
    from backend.uefn_plugins.ui_kit import LIVE_PRESENCE_SCRIPT
    from backend.uefn_plugins.webview import with_panel_error_script

    out = with_panel_error_script(b"<!doctype html><html><head></head><body></body></html>")
    assert LIVE_PRESENCE_SCRIPT.encode() in out
    for need in ("live.pointer", "live.select", "live.hold", "ducky.hold", "live.state", "live.message", "live.peers",
                 "sync.state", "pointer-events:none"):
        assert need in LIVE_PRESENCE_SCRIPT, need
