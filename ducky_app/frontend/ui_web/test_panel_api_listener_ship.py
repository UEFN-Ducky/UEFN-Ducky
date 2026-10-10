"""The listener-online re-ship fires once per real offline -> online change, app-wide."""
from frontend import ship_newest
from frontend.ui_web import panel_api


def test_new_panel_api_per_request_does_not_reship_every_poll(monkeypatch):
    # Oct 10 2026: every HTTP / phone / website request builds a new PanelApi. With the
    # flag on the instance, each of their status polls re-shipped every skill pack to every
    # IDE every 5 s, and Ducky kept two cores busy while idle.
    shipped = []
    monkeypatch.setattr(ship_newest, "ship_newest_everywhere_async", lambda **kw: shipped.append(kw))
    monkeypatch.setattr(panel_api, "_listener_was_online", False)
    online, offline = {"online": True, "wedged": False}, {"online": False}

    def poll(status):  # what one request's fresh PanelApi does
        panel_api.PanelApi._maybe_ship_on_listener_online(object(), status)

    for _ in range(5):
        poll(online)
    assert len(shipped) == 1
    poll(offline)
    poll(online)
    assert len(shipped) == 2
    poll({"online": True, "wedged": True})  # a wedged listener is not online
    poll(online)
    assert len(shipped) == 3


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
