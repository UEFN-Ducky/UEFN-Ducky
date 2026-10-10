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
