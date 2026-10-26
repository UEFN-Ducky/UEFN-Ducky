"""Live groups across PCs: each PC its own process with its own database, device and person,
running the real core (``live``, the version engine, plugin data) and driven through the
panel API methods a plugin's pages call. The test joins them like the services would:

- the team server (:class:`FakeLedgerServer`): each PC syncs and beats presence with it, and
  a PC whose presence changed has the others beat again (as the next heads would show it);
- the live link: a frame goes to the PCs live in the sender's room, or to one PC (``to``),
  and arrives as the provider's ``deliver``.

What is checked is what the people see: Start live puts them in one group, every plugin of the
team copy with it, shared values and live states go both ways through the leader, a
groupmate's change reaches the group at once and goes to the server from the leader under its
maker's name, and the group passes on when its host leaves and ends for the last one in it.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Callable

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI")

APP = Path(__file__).resolve().parents[2]  # ducky_app
SPACE, PLUGIN, NOTES = "spaceT", "cards", "notes"
AUTH = b"ducky-live-pcs"
WAIT_S = 20.0
LEDGER_METHODS = ("capabilities", "heads", "push", "pull", "fetch", "history", "revert", "lock", "unlock",
                  "break_lock", "ask", "beat")


# --------------------------------------------------------------------------- one PC (a child process)


def pc_main(address: tuple[str, int], name: str, app: str = "", account_name: str = "") -> None:
    """A PC: signs in as ``name``, has ``cards`` and ``notes`` on the team copy, and does what
    the test says.
    ``app``: an older app. "1.2.434" runs its group through a leader like this one, but its
    presence names no version. "1.2.433" is from before that: its team service says its
    version, its live changes say nothing of who made them, and it never holds a change for a
    leader or sends one for a groupmate."""
    import frontend

    from frontend import duckyos_account
    from frontend.ui_web import panel_api  # noqa: F401  (panel_api_store loads with it)
    from frontend.ui_web.panel_api_store import PanelApiStoreMixin

    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs
    from backend.uefn_plugins import data_crypto, host, live, scopes, spaces, sync_scheduler, vcs_engine
    from backend.uefn_plugins.ledger import OutgoingChangeset
    from backend.uefn_plugins.scopes import PluginData

    who = account_name or name  # one account on two PCs: the same person, two devices
    key = f"https://uefnducky.org|{who.lower()}@example.com"
    user = "u_" + who.lower()
    duckyos_account.account_key = lambda blob=None: key
    data_crypto._fetch_adk = lambda: hashlib.sha256(b"adk/" + key.encode()).digest()
    vcs_engine.my_user_id = lambda: user
    vcs_engine.device_label = lambda: f"{name}-PC"
    account = scopes.account_id()
    device = space_vcs.device_id()

    def dial(role: str) -> Any:
        conn = Client(address, authkey=AUTH)
        conn.send(("hello", name, role, device, user, who))
        return conn

    svc, evt, ctl = dial("svc"), dial("evt"), dial("ctl")
    svc_lock = threading.Lock()

    def rpc(*req: Any) -> Any:
        with svc_lock:
            svc.send(req)
            ok, res = svc.recv()
        if ok != "ok":
            raise RuntimeError(res)
        return res

    def plain(a: Any) -> Any:  # an upload's contents read here (a reader can't cross processes)
        if isinstance(a, OutgoingChangeset):
            return dataclasses.replace(a, items=tuple(
                dataclasses.replace(i, data=i.read(), reader=None, path="") if i.op == "put" else i
                for i in a.items))
        return a

    def call(method: str) -> Callable[..., Any]:
        return lambda self, *args, **kwargs: rpc("ledger", method, tuple(plain(a) for a in args), kwargs)

    # The team server, from this PC: each call runs on the hub's FakeLedger client.
    remote = type("Remote", (), {m: call(m) for m in LEDGER_METHODS})()

    class Provider(spaces.Provider):
        kind_label = "team"

        def enabled(self) -> bool:
            return True

        def ledger(self, account: str, space_id: str, plugin: str) -> Any:
            return remote

        def live_supported(self) -> bool:
            return True

        def live_join(self, account: str, space_id: str, plugin: str, room: str) -> None:
            rpc("room", plugin, room)

        def live_leave(self, account: str, space_id: str, plugin: str) -> None:
            rpc("room", plugin, "")

        def live_send(self, account: str, space_id: str, plugin: str, topic: str, data: Any, *, to: str = "",
                      fast: bool = False) -> int:
            if app == "1.2.433" and topic == live.DATA:
                data = {k: v for k, v in data.items() if k != "by"}
            return int(rpc("send", plugin, topic, json.loads(json.dumps(data)), to))

        def live_state(self, account: str, space_id: str, plugin: str) -> dict[str, Any]:
            return {"state": "live", "peers": rpc("peers", plugin)}

    spaces.install_for_tests(Provider())
    for pid in (PLUGIN, NOTES):
        repo.link_set(account, pid, SPACE)
        space_vcs.head_put(account, SPACE, pid, engine="v2")
    repo.sync_put(account, SPACE, label="Alpha", synced_at=time.time())
    live._enabled = host.is_plugin_enabled = lambda plugin: True
    scopes.data_live = lambda plugin: plugin == PLUGIN
    scopes.data_live_asked = lambda plugin: plugin == PLUGIN  # its manifest says "data_live": true, like IMK's
    live._push = lambda event: None
    if app:
        frontend.__version__ = app if app == "1.2.433" else ""
        room_of = live.room_of
        live.room_of = lambda *a: None if (r := room_of(*a)) is None else {k: v for k, v in r.items() if k != "v"}
        live.team_room = lambda *a: None  # its presence says the room per plugin only
    if app == "1.2.433":
        live.hold_since = lambda *a, **k: None
        live.leads = live._leads_group = lambda *a, **k: False
    rounds = threading.Lock()

    def sync(plugin: str = PLUGIN) -> dict[str, Any]:
        with rounds:
            return vcs_engine.run(account, SPACE, plugin, remote, force=True)

    def beat() -> dict[str, Any]:
        return vcs_engine.beat(account, SPACE, remote, [PLUGIN, NOTES], force=True)

    def later(fn: Callable[[], Any]) -> None:
        def run() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — the test sees what didn't happen
                print(f"{name}: {getattr(fn, '__name__', fn)} failed: {exc!r}", flush=True)

        threading.Thread(target=run, daemon=True).start()

    sync_scheduler.wake = lambda a, s, p=None: later(lambda: sync(p or PLUGIN))

    def events() -> None:
        while True:
            try:
                msg = evt.recv()
            except (EOFError, OSError):
                return
            if msg[0] == "frame":
                try:
                    live.deliver(account, SPACE, msg[1], msg[2], msg[3], msg[4])
                except Exception as exc:  # noqa: BLE001
                    print(f"{name}: deliver {msg[3]} failed: {exc!r}", flush=True)
            elif msg[0] == "rebeat":
                later(beat)

    threading.Thread(target=events, daemon=True).start()
    data = PluginData(PLUGIN)
    api = PanelApiStoreMixin()

    def ai_put(k: str, v: Any, ducky: str) -> None:  # one of this person's duckies, from a tool call
        with scopes.write_group(PLUGIN, actor="agent", ducky=ducky, message="cards_tune"):
            data.put(k, v)

    def row(k: str) -> dict[str, Any] | None:
        r = repo.get(account, SPACE, PLUGIN, "doc", k)
        return None if r is None else {"dirty": int(r["dirty"]), "rev": int(r["rev"])}

    def close_tab(plugin: str) -> None:  # every tab of the plugin, as the window reports it
        tab = f"plugin:{plugin}:main"
        PanelApiWindowMixin.report_plugin_panel(None, "main", tab, visible=True, open=True)  # type: ignore[arg-type]
        PanelApiWindowMixin.report_plugin_panel(None, "main", tab, visible=False, open=False)  # type: ignore[arg-type]

    commands: dict[str, Callable[..., Any]] = {
        "start": lambda plugin=PLUGIN: api.plugin_live_join(plugin),
        "leave": lambda plugin=PLUGIN: api.plugin_live_leave(plugin),
        "status": lambda plugin=PLUGIN: api.plugin_live_status(plugin),
        "overview": api.live_overview,
        "close_tab": close_tab,
        "reload": host.invalidate_plugin_runtime,
        "put": data.put,
        "put_file": lambda k, b64: data.put_file(k, base64.b64decode(b64)),
        "get_file": lambda k: base64.b64encode(data.get_file(k) or b"").decode(),
        "ai_put": ai_put,
        "get": data.get,
        "row": row,
        "sync": sync,
        "beat": beat,
        "share": lambda k, v: api.plugin_live_share(PLUGIN, k, v),
        "shared": lambda: api.plugin_live_shared(PLUGIN)["values"],
        "set_state": lambda s: api.plugin_live_set_my_state(PLUGIN, s),
        "member_states": lambda: api.plugin_live_member_states(PLUGIN)["states"],
        "history": lambda: spaces.team_history(PLUGIN, limit=10)["entries"],
    }
    beat()
    ctl.send(("ok", "ready"))
    while True:
        try:
            cmd, args = ctl.recv()
        except (EOFError, OSError):
            break
        if cmd == "exit":
            live.leave_all()
            ctl.send(("ok", None))
            break
        try:
            ctl.send(("ok", commands[cmd](*args)))
        except Exception as exc:  # noqa: BLE001 — handed to the test
            ctl.send(("err", f"{type(exc).__name__}: {exc}"))
    os._exit(0)


# --------------------------------------------------------------------------- the hub (the test process)


class PC:
    """A PC as the test drives it: ``pc("put", "card.a", {...})``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.device = self.user = self.person = ""
        self.rooms: dict[str, str] = {}  # plugin -> the live room it is in
        self.presence: Any = None
        self.ctl: Any = None
        self.evt: Any = None
        self.ledger: Any = None
        self.evt_lock = threading.Lock()
        self.proc: subprocess.Popen[bytes] | None = None

    def __call__(self, cmd: str, *args: Any) -> Any:
        self.ctl.send((cmd, args))
        ok, res = self.ctl.recv()
        if ok != "ok":
            raise AssertionError(f"{self.name} {cmd}: {res}")
        return res

    def who(self) -> dict[str, str]:
        return {"name": self.person or self.name, "user": self.user, "device": self.device, "label": f"{self.name}-PC"}

    def tell(self, *msg: Any) -> None:
        with self.evt_lock:
            self.evt.send(msg)


class Hub:
    def __init__(self, tmp: Path) -> None:
        from backend.uefn_plugins.ledger_conformance import FakeLedgerServer

        self.server = FakeLedgerServer()
        self.listener = Listener(("127.0.0.1", 0), authkey=AUTH)
        self.pcs: dict[str, PC] = {}
        self.tmp = tmp
        self.quiet = False  # teammates don't see presence change until :meth:`speak`
        threading.Thread(target=self._accept, daemon=True).start()

    def speak(self) -> None:
        """Presence goes round again: every PC beats and sees everyone's."""
        self.quiet = False
        for pc in self.pcs.values():
            pc.tell("rebeat")

    def _accept(self) -> None:
        while True:
            try:
                conn = self.listener.accept()
            except OSError:
                return
            _, name, role, device, user, person = conn.recv()
            pc = self.pcs[name]
            pc.device, pc.user, pc.person = device, user, person
            if role == "svc":
                pc.ledger = self.server.client(user, device, name=person)
                threading.Thread(target=self._serve, args=(pc, conn), daemon=True).start()
            elif role == "evt":
                pc.evt = conn
            else:
                pc.ctl = conn

    def _serve(self, pc: PC, conn: Any) -> None:
        while True:
            try:
                req = conn.recv()
            except (EOFError, OSError):
                return
            try:
                res = ("ok", self._handle(pc, req))
            except Exception as exc:  # noqa: BLE001 — the PC raises it
                res = ("err", f"{type(exc).__name__}: {exc}")
            conn.send(res)

    def _handle(self, pc: PC, req: tuple[Any, ...]) -> Any:
        kind = req[0]
        if kind == "ledger":
            method, args, kwargs = req[1], req[2], req[3]
            got = getattr(pc.ledger, method)(*args, **kwargs)
            if method == "beat" and args[1] != pc.presence and not self.quiet:
                pc.presence = args[1]
                for other in self.pcs.values():  # teammates see it at their next look: now
                    if other is not pc and other.evt is not None:
                        other.tell("rebeat")
            return got
        if kind == "room":
            pc.rooms[str(req[1])] = str(req[2])
            return None
        if kind == "peers":
            return [{**p.who(), "path": "direct"} for p in self._room(pc, str(req[1]))]
        if kind == "send":
            plugin, topic, data, to = req[1], req[2], req[3], req[4]
            targets = [p for p in self._room(pc, plugin) if not to or p.device == to]
            for p in targets:
                p.tell("frame", plugin, pc.who(), topic, data)
            return len(targets)
        raise ValueError(f"unknown request {kind}")

    def _room(self, pc: PC, plugin: str) -> list[PC]:
        room = pc.rooms.get(plugin)
        return [p for p in self.pcs.values() if p is not pc and room and p.rooms.get(plugin) == room]

    def start(self, name: str, *, app: str = "", account: str = "") -> PC:
        pc = self.pcs[name] = PC(name)
        root = self.tmp / name
        (root / "Roaming").mkdir(parents=True)
        env = {**os.environ, "LOCALAPPDATA": str(root), "APPDATA": str(root / "Roaming"),
               "UEFN_DUCKY_DOCUMENTS_DIR": str(root / "docs"), "PYTHONPATH": str(APP)}
        env.pop("UEFN_DUCKY_MCP_BRIDGE", None)
        code = ("import sys, importlib.util; sys.path.insert(0, sys.argv[1]);"
                "spec = importlib.util.spec_from_file_location('live_pcs', sys.argv[2]);"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
                "m.pc_main((sys.argv[3], int(sys.argv[4])), sys.argv[5], sys.argv[6], sys.argv[7])")
        host, port = self.listener.address
        pc.proc = subprocess.Popen([sys.executable, "-c", code, str(APP), str(Path(__file__).resolve()), host,
                                    str(port), name, app, account], cwd=str(root), env=env)
        deadline = time.monotonic() + 60
        while pc.ctl is None:
            if time.monotonic() > deadline or pc.proc.poll() is not None:
                raise AssertionError(f"{name} didn't start")
            time.sleep(0.05)
        ok, res = pc.ctl.recv()
        assert (ok, res) == ("ok", "ready"), res
        return pc

    def close(self) -> None:
        for pc in self.pcs.values():
            try:
                pc("exit")
            except Exception:  # noqa: BLE001 — gone already
                pass
            if pc.proc is not None:
                try:
                    pc.proc.wait(10)
                except subprocess.TimeoutExpired:
                    pc.proc.kill()
        self.listener.close()
        self.server.rmtree()


def until(check: Callable[[], Any], what: str, timeout: float = WAIT_S) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


@pytest.fixture
def hub(tmp_path: Path):
    h = Hub(tmp_path)
    yield h
    h.close()


def _mine(pc: PC) -> dict[str, Any]:
    return next((g for g in pc("status")["groups"] if g["mine"]), {})


def _go_live(pc: PC, host: PC) -> None:
    """``pc`` presses Start live once it sees ``host``'s group: it goes into it."""
    until(lambda: any(g["room"] == f"h:{host.device}" for g in pc("status")["groups"]),
          f"{pc.name} to see {host.name}'s group")
    assert pc("start")["room"] == f"h:{host.device}"


# --------------------------------------------------------------------------- what the people see


def test_two_pcs_go_live_together_share_and_sync_through_the_leader(hub: Hub) -> None:
    ana, ben = hub.start("Ana"), hub.start("Ben")
    # Connect: Ana starts live; Ben's Start live goes into her group, not a second one.
    assert ana("start")["room"] == f"h:{ana.device}"
    _go_live(ben, ana)
    until(lambda: len(_mine(ana).get("members", [])) == 2, "Ana's group to have Ben")
    mine = _mine(ben)
    assert mine["hostDevice"] == ana.device and not mine["hostIsMe"] and _mine(ana)["hostIsMe"]
    assert [(p["name"], p["path"]) for p in ben("status")["peers"]] == [("Ana", "direct")]
    # API: a shared value goes both ways through the leader; each PC's live state reaches the other.
    assert ana("share", "player", {"playing": True, "t": 12})["ok"]
    until(lambda: (ben("shared").get("player") or {}).get("value") == {"playing": True, "t": 12},
          "Ben to get the player")
    assert ben("share", "clip", "c2")["ok"]
    until(lambda: (ana("shared").get("clip") or {}).get("value") == "c2", "Ana to get Ben's clip")
    assert ana("shared")["clip"]["by"]["name"] == "Ben" and ben("shared")["clip"]["value"] == "c2"
    assert ben("set_state", {"cam": [1, 2, 3]})["ok"]
    until(lambda: [s["state"] for s in ana("member_states")] == [{"cam": [1, 2, 3]}], "Ana to see Ben's camera")
    # Data: Ben's change reaches Ana at once; Ana, the leader, sends it under Ben's name.
    ben("put", "card.pip", {"hp": 3})
    until(lambda: ana("get", "card.pip") == {"hp": 3}, "Ana to get Ben's change live")
    assert ana("row", "card.pip")["dirty"] == 1  # the leader's to send
    ben("sync")
    assert not hub.server.space(SPACE).repo(PLUGIN).changesets  # Ben held it: the leader sends it
    assert ana("sync")["pushed"] == 1
    until(lambda: (ben("sync"), ben("row", "card.pip")["dirty"] == 0)[1], "the leader's commit to settle Ben's")
    (entry,) = [e for e in ben("history") if ["doc", "card.pip"] in e["items"]]
    assert (entry["by"], entry["via"], entry["device"], entry["actor"]) == ("Ben", "Ana", "Ben-PC", "user")
    # A file too big to go live: Ana can't send it for Ben, so Ben sends it at once and Ana pulls it.
    clip = base64.b64encode(b"v" * 64 * 1024).decode()
    ben("put_file", "art/clip.webm", clip)
    sent = until(lambda: (ben("sync"), [e for e in ben("history") if ["asset", "art/clip.webm"] in e["items"]])[1],
                 "Ben to send his file (well within the 90 s a held change waits)")
    assert (sent[0]["by"], sent[0].get("via")) == ("Ben", None)
    until(lambda: (ana("sync"), ana("get_file", "art/clip.webm") == clip)[1], "Ana to get Ben's file")
    # Ben's ducky: its change goes through the leader too, and History says it was Ben's AI.
    ben("ai_put", "card.ai", {"hp": 9}, "Verse Generator")
    until(lambda: ana("get", "card.ai") == {"hp": 9}, "Ana to get the ducky's change live")
    assert ana("sync")["pushed"] == 1
    (entry,) = [e for e in ben("history") if ["doc", "card.ai"] in e["items"]]
    assert (entry["by"], entry["via"], entry["actor"], entry["ducky"]) == ("Ben", "Ana", "agent", "Verse Generator")
    # Ana's own change: hers, sent by her, at Ben's at once.
    ana("put", "card.zed", {"hp": 1})
    until(lambda: ben("get", "card.zed") == {"hp": 1}, "Ben to get Ana's change live")
    assert ben("row", "card.zed")["dirty"] == 0
    ana("sync")
    newest = ana("history")[0]
    assert ["doc", "card.zed"] in newest["items"] and newest["by"] == "Ana" and "via" not in newest


def test_the_group_passes_on_when_its_host_leaves_and_ends_for_the_last_one(hub: Hub) -> None:
    ana, ben, cy = hub.start("Ana"), hub.start("Ben"), hub.start("Cy")
    ana("start")
    _go_live(ben, ana)
    until(lambda: len(_mine(ana).get("members", [])) == 2, "Ben in Ana's group")
    _go_live(cy, ana)  # Ben joined first: he has been in it longest
    until(lambda: len(_mine(ana).get("members", [])) == 3, "all three in Ana's group")
    ana("leave")
    until(lambda: _mine(cy).get("hostDevice") == ben.device, "the group to pass to Ben")
    assert _mine(ben)["hostIsMe"]
    assert not ana("status")["on"] and ana("status")["rejoin"] == ""
    # Ben leads now: Cy's value goes through him.
    assert cy("share", "clip", "c7")["ok"]
    until(lambda: (ben("shared").get("clip") or {}).get("value") == "c7", "Ben to take Cy's value")
    ben("leave")
    until(lambda: cy("status")["on"] is False, "Live to end for Cy, left alone")
    assert cy("status")["rejoin"] == ""


def test_two_pcs_that_start_live_at_once_end_up_in_one_group(hub: Hub) -> None:
    ana, ben = hub.start("Ana"), hub.start("Ben")
    hub.quiet = True
    assert ana("start")["room"] == f"h:{ana.device}"
    assert ben("start")["room"] == f"h:{ben.device}"  # neither saw the other yet
    hub.speak()
    low = min((ana, ben), key=lambda p: p.device)
    until(lambda: _mine(ana).get("room") == _mine(ben).get("room") == f"h:{low.device}", "one group")
    assert len(_mine(ana)["members"]) == len(_mine(ben)["members"]) == 2


def test_an_older_app_hosts_the_group_the_up_to_date_pc_leads_and_each_sends_its_own(hub: Hub) -> None:
    laptop = hub.start("Iliya", app="1.2.433")
    tas = hub.start("Tas")
    assert laptop("start")["ok"]
    _go_live(tas, laptop)  # Start live goes into the older PC's group
    until(lambda: len(_mine(tas).get("members", [])) == 2, "Tas in the Laptop's group")
    mine = _mine(tas)
    assert mine["hostIsMe"] and [(m["name"], m["older"]) for m in mine["members"] if not m["me"]] == [("Iliya", True)]
    # Tas's change: at the Laptop at once, and out from Tas at once (nobody to hold it for).
    tas("put", "card.pip", {"hp": 3})
    until(lambda: laptop("get", "card.pip") == {"hp": 3}, "the Laptop to get Tas's change live")
    assert tas("sync")["pushed"] == 1
    # The Laptop's change: at Tas's at once as a pulled copy; the Laptop's app sends it.
    laptop("put", "card.zed", {"hp": 1})
    until(lambda: tas("get", "card.zed") == {"hp": 1}, "Tas to get the Laptop's change live")
    assert tas("row", "card.zed")["dirty"] == 0
    laptop("sync")  # (Tas's commit note may have had it pull and send already)
    made = {tuple(e["items"][0]): (e["by"], e.get("via")) for e in tas("history")}
    assert made[("doc", "card.pip")] == ("Tas", None) and made[("doc", "card.zed")] == ("Iliya", None)


def test_a_pc_that_says_no_version_still_leads_its_group_like_1_2_434(hub: Hub) -> None:
    ana = hub.start("Ana", app="1.2.434")
    ben = hub.start("Ben")
    assert ana("start")["ok"]
    _go_live(ben, ana)
    until(lambda: len(_mine(ana).get("members", [])) == 2, "Ben in Ana's group")
    mine = _mine(ben)
    assert mine["hostDevice"] == ana.device and not any(m["older"] for m in mine["members"])
    ben("put", "card.pip", {"hp": 3})
    until(lambda: ana("get", "card.pip") == {"hp": 3}, "Ana to get Ben's change live")
    assert ana("row", "card.pip")["dirty"] == 1  # Ana leads: hers to send, for Ben
    ben("sync")
    assert not hub.server.space(SPACE).repo(PLUGIN).changesets  # Ben held it for Ana
    assert ana("sync")["pushed"] == 1
    (entry,) = until(lambda: [e for e in ben("history") if ["doc", "card.pip"] in e["items"]], "the change in history")
    assert (entry["by"], entry["via"]) == ("Ben", "Ana")


def test_one_account_on_two_pcs_streams_like_two_people_and_history_names_the_pc(hub: Hub) -> None:
    main, lap = hub.start("Main", account="Tas"), hub.start("Lap", account="Tas")
    assert main("start")["ok"]
    _go_live(lap, main)
    until(lambda: len(_mine(main).get("members", [])) == 2, "the laptop in the main PC's group")
    assert [m["name"] for m in _mine(lap)["members"] if not m["me"]] == ["You on Main-PC"]
    assert lap("share", "clip", "c3")["ok"]
    until(lambda: (main("shared").get("clip") or {}).get("value") == "c3", "the main PC to get the laptop's value")
    lap("put", "card.pip", {"hp": 4})
    until(lambda: main("get", "card.pip") == {"hp": 4}, "the main PC to get the laptop's change live")
    assert main("sync")["pushed"] == 1  # the main PC leads: it sends it
    (entry,) = until(lambda: [e for e in lap("history") if ["doc", "card.pip"] in e["items"]], "the change in history")
    assert (entry["by"], entry["device"], "via" in entry) == ("Tas", "Lap-PC", False)  # Tas on the laptop


def test_one_start_live_covers_every_plugin_and_closing_a_tab_or_a_reload_keeps_it(hub: Hub) -> None:
    """Live is the team copy's: Start live from Cards puts Notes in the group too, someone in
    another plugin is still in it, and neither a closed tab nor a plugin reload ends it."""
    ana, ben = hub.start("Ana"), hub.start("Ben")
    room = ana("start")["room"]
    assert room == f"h:{ana.device}"
    assert ana("status", NOTES)["on"] and ana("status", NOTES)["room"] == room
    hub.speak()
    until(lambda: any(g["room"] == room for g in ben("overview")["teams"][0]["groups"]), "Ben to see Ana's group")
    assert ben("start", NOTES)["room"] == room  # from another plugin: the same group
    assert ben("status")["on"] and ben("status")["room"] == room
    def with_ben() -> dict[str, Any] | None:
        t = ana("overview")["teams"][0]
        return t if len(t["groups"][0]["members"]) == 2 else None

    team = until(with_ben, "Ana's menu to show Ben in her group")
    assert team["on"] and [m["name"] for m in team["groups"][0]["members"]] == ["You", "Ben"]
    ben("close_tab", PLUGIN)
    ben("reload", NOTES)
    time.sleep(0.5)
    assert ben("status")["on"] and ben("status", NOTES)["on"]
    ana("share", "clip", "intro")  # the group still runs through Ana
    until(lambda: ben("shared").get("clip", {}).get("value") == "intro", "Ben to get Ana's value")
    ben("leave", NOTES)  # Leave from either plugin: out of the group, every plugin with it
    assert not ben("status")["on"] and not ben("status", NOTES)["on"]
