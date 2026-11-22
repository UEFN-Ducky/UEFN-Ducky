"""Alerts: batched for the account, merged on screen, pushed to the phone only when away."""

from __future__ import annotations

import pytest

from frontend import duckyos_account, notifications


@pytest.fixture()
def hub(monkeypatch):
    notifications.reset_for_tests()
    batches: list[list[dict]] = []
    toasts: list[tuple[str, str]] = []
    opened: list[tuple[str, str]] = []
    clicks: list = []
    state = {"front": False, "idle": 600.0}

    def toast(title, text, on_click):
        toasts.append((title, text))
        clicks.append(on_click)
        return True

    monkeypatch.setattr(notifications, "_app_in_front", lambda: state["front"])
    monkeypatch.setattr(notifications, "_idle_seconds", lambda: state["idle"])
    monkeypatch.setattr(duckyos_account, "send_account_alerts", lambda items: batches.append(items) or True)
    notifications.bind_desktop(toast, lambda kind, tid: opened.append((kind, tid)))
    yield {"batches": batches, "toasts": toasts, "opened": opened, "clicks": clicks, "state": state}
    notifications.reset_for_tests()
    notifications.bind_desktop(None, None)


def test_one_batch_one_row_per_target_newest_wins(hub) -> None:
    notifications.alert("Build island", "Needs your answer.", level="ask", kind="chat", target_id="c1")
    notifications.alert("Build island", "Finished.", level="ok", kind="chat", target_id="c1")
    notifications.alert("Daily check", "Workflow failed.", level="error", kind="workflow", target_id="w1")
    assert hub["batches"] == []  # nothing leaves until the batch window ends
    notifications.flush_for_tests()
    assert len(hub["batches"]) == 1
    rows = hub["batches"][0]
    assert [(r["id"], r["body"]) for r in rows] == [("c1", "Finished."), ("w1", "Workflow failed.")]
    assert all(r["push"] for r in rows)


def test_a_settings_alert_opens_its_tab_on_this_pc_and_names_no_target_to_the_account(hub) -> None:
    notifications.alert("UEFN Ducky", "Your PC is running low on RAM.", level="error",
                        kind="settings", target_id="General", detail="Low RAM: 1.1 GB free.")
    hub["clicks"][0]()
    assert hub["opened"] == [("settings", "General")]
    notifications.flush_for_tests()
    row = hub["batches"][0][0]
    assert (row["kind"], row["id"], row["body"]) == ("", "", "Your PC is running low on RAM.")


def test_the_same_alert_twice_is_one(hub) -> None:
    assert notifications.alert("Roguelike", "Failed.", level="error") is True
    assert notifications.alert("Roguelike", "Failed.", level="error") is False
    notifications.flush_for_tests()
    assert len(hub["batches"][0]) == 1


def test_at_the_pc_no_phone_push_and_in_front_no_toast(hub) -> None:
    hub["state"].update(front=True, idle=5.0)
    notifications.alert("Build island", "Finished.", kind="chat", target_id="c1")
    notifications.flush_for_tests()
    assert hub["toasts"] == []
    assert hub["batches"][0][0]["push"] is False  # still listed on the profile's bell

    hub["state"].update(front=False, idle=5.0)
    notifications.alert("Artist", "Needs your answer.", level="ask", kind="chat", target_id="c2", detail="Which map?")
    notifications.flush_for_tests()
    assert hub["toasts"] == [("Artist", "Which map?")]  # the question stays on this PC
    assert hub["batches"][1][0]["push"] is False
    assert "Which map" not in repr(hub["batches"])


def test_close_alerts_merge_into_one_notification(hub) -> None:
    notifications.alert("Build island", "Finished.", kind="chat", target_id="c1")
    notifications.alert("Daily check", "Workflow failed.", level="error", kind="workflow", target_id="w1")
    notifications.alert("Artist", "Needs your answer.", level="ask", kind="chat", target_id="c2")
    assert len(hub["toasts"]) == 1  # the first shows at once, the rest wait
    notifications.flush_for_tests()
    assert len(hub["toasts"]) == 2
    title, text = hub["toasts"][1]
    assert title == "2 updates" and text.startswith("Artist")
    hub["clicks"][1]()
    assert hub["opened"] == [("chat", "c2")]  # a merged click opens the question


def test_signed_out_sends_nothing(monkeypatch) -> None:
    monkeypatch.setattr(duckyos_account, "_load_blob", lambda: {})
    called: list = []
    monkeypatch.setattr(duckyos_account, "_plugin_collect", lambda *a, **k: called.append(a))
    assert duckyos_account.send_account_alerts([{"title": "x", "body": "y"}]) is False
    assert called == []


def test_old_site_gets_one_phone_push_per_batch(monkeypatch) -> None:
    monkeypatch.setattr(duckyos_account, "_load_blob", lambda: {"device_key": "k", "email": "a@b.c", "device_key_id": "key1"})
    sent: list[tuple[str, dict]] = []

    def collect(plugin_id, event, body=None, **_kw):
        sent.append((event, dict(body or {})))
        if event == "desktop-notify":
            raise duckyos_account.DuckyOSAccountError("gone", code="push_unavailable")
        return {}

    monkeypatch.setattr(duckyos_account, "_plugin_collect", collect)
    items = [
        {"title": "A", "body": "Finished.", "kind": "chat", "id": "c1", "push": True},
        {"title": "B", "body": "Workflow failed.", "kind": "workflow", "id": "w1", "push": True},
        {"title": "C", "body": "Finished.", "push": False},
    ]
    assert duckyos_account.send_account_alerts(items) is True
    assert [e for e, _ in sent] == ["desktop-notify", "desktop-agent-done"]
    assert sent[0][1]["keyId"] == "key1" and len(sent[0][1]["items"]) == 3
    assert sent[1][1] == {"title": "UEFN Ducky", "body": "2 updates on your PC."}


def test_linux_notify_open_action_passes_activation_token(monkeypatch) -> None:
    """Fake session bus: Notify gets an Open action; ActionInvoked opens with the token set."""
    from frontend import open_files

    notifications.reset_for_tests()
    calls: list[tuple] = []
    monkeypatch.setattr(open_files, "gdbus_call", lambda *a, **_k: calls.append(a) or "(uint32 42,)")
    monkeypatch.setattr(open_files, "gdbus_monitor", lambda *a, **_k: None)
    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    opened: list[str] = []
    assert notifications.linux_toast("Agent finished", "a <b> & c", lambda: opened.append("c1")) is True
    dest, path, method, *args = calls[0]
    assert (dest, method) == ("org.freedesktop.Notifications", "org.freedesktop.Notifications.Notify")
    assert args[3] == "'Agent finished'" and args[4] == "'a &lt;b&gt; &amp; c'"
    assert args[5] == "['default', 'Open', 'open', 'Open']"
    # Another notification's click is not ours; a close without a click forgets it.
    notifications.handle_linux_signal("/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 7, 'open')")
    assert opened == []
    notifications.handle_linux_signal("/org/freedesktop/Notifications: org.freedesktop.Notifications.ActivationToken (uint32 42, 'tok-1')")
    notifications.handle_linux_signal("/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 42, 'open')")
    assert opened == ["c1"]
    import os

    assert os.environ["XDG_ACTIVATION_TOKEN"] == "tok-1"
    notifications.handle_linux_signal("/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 42, 'open')")
    assert opened == ["c1"]  # once


def test_linux_notify_fails_soft_and_binds_without_a_tray(monkeypatch) -> None:
    import sys

    from frontend import open_files

    monkeypatch.setattr(open_files, "gdbus_call", lambda *a, **_k: None)  # no bus / no server
    monkeypatch.setattr(open_files, "gdbus_monitor", lambda *a, **_k: None)
    assert notifications.linux_toast("t", "m") is False
    monkeypatch.setattr(sys, "platform", "linux")
    notifications.bind_desktop(None, None)
    assert notifications._toast_fn is notifications.linux_toast
    monkeypatch.setattr(sys, "platform", "win32")
    notifications.bind_desktop(None, None)
    assert notifications._toast_fn is None
