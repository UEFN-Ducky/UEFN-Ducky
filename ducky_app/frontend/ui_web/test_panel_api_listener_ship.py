"""Reading the listener status never ships, revives or contacts UEFN (decision 2026-11-03)."""
import threading

from backend.uefn_plugins.plugin_host.testing import patch_host
from frontend import ship_newest
from frontend.ui_web import panel_api


def test_status_reads_and_refreshes_never_ship_or_revive(monkeypatch):
    # Oct 10 2026: every status poll that saw the listener come online re-shipped every skill
    # pack to every IDE. Nov 3 2026: a ship runs only at app start and on Settings -> Apply.
    from backend.bridge import revive

    shipped, revived = [], []
    monkeypatch.setattr(ship_newest, "ship_newest_everywhere", lambda **kw: shipped.append(kw) or [])
    monkeypatch.setattr(ship_newest, "ship_newest_everywhere_async", lambda **kw: shipped.append(kw))
    monkeypatch.setattr(revive.AutoRevive, "observe", lambda *a, **k: revived.append(1))
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._listener_status_lock = threading.Lock()
    flips = iter([{"online": False}, {"online": True, "wedged": False}] * 5)
    done = threading.Event()
    fetched = []

    def fetch():
        fetched.append(1)
        if len(fetched) == 10:
            done.set()
        return dict(next(flips))

    monkeypatch.setattr(api, "_fetch_listener_status", fetch, raising=False)
    monkeypatch.setattr(api, "_push_panel", lambda event: None, raising=False)
    patch_host(monkeypatch, "attach_plugin_connections", lambda s: s, raising=False)
    for _ in range(10):  # ten offline/online flips, each from a refresh
        api.get_listener_status(refresh=True)
        while api._listener_status_cache()._inflight:
            threading.Event().wait(0.01)
    assert done.wait(5)
    assert shipped == [] and revived == []


def test_status_read_shows_live_plugin_rows_without_fetching_uefn(monkeypatch):
    # Nov 5 2026: Blender opened after the last UEFN fetch kept saying Offline in the header,
    # and a plugin loaded after that fetch had no row, until UEFN was checked again.
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._listener_status_lock = threading.Lock()
    fetched = []
    monkeypatch.setattr(api, "_fetch_listener_status", lambda: fetched.append(1) or {}, raising=False)
    blender = {"online": False}
    patch_host(
        monkeypatch,
        "attach_plugin_connections",
        lambda s: {**s, "plugin_connections": [{"id": "blender", "online": blender["online"]}]},
        raising=False,
    )
    assert api.get_listener_status()["plugin_connections"] == [{"id": "blender", "online": False}]
    blender["online"] = True  # Blender opened: the next read says so
    assert api.get_listener_status()["plugin_connections"] == [{"id": "blender", "online": True}]
    assert fetched == []

def test_switching_or_removing_a_project_forgets_the_last_projects_listener_state(monkeypatch):
    # Oct 10 2026: a switch reset two attributes nothing read, so a listener that reported
    # no project fields kept showing the previous project's UEFN name and folder.
    from backend.bridge.status import ListenerStatusState
    from frontend.ui_web import project_switch

    monkeypatch.setattr(panel_api, "switch_panel_project", lambda **kw: {"ok": True})
    monkeypatch.setattr(project_switch, "delete_panel_project", lambda path, push_ui=True: {"ok": True})
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    old = {"at": 1.0, "uefn_project_dir": "C:/Old", "uefn_project_name": "Old", "project_match": True}

    for switch in (lambda: api.set_project_root("C:/New"), lambda: api.delete_recent_project("C:/Old")):
        api._listener_status_state = ListenerStatusState(ping_fail_streak=1, project_cache=dict(old))
        switch()
        assert api._listener_status_state.project_cache is None
        assert api._listener_status_state.ping_fail_streak == 0
