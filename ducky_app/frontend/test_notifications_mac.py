"""macOS notifications: UNUserNotificationCenter in the app, osascript when unsigned/dev."""
from __future__ import annotations

import sys
import types

import pytest

from frontend import notifications as n


class _Content:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setTitle_(self, t):
        self.title = t

    def setBody_(self, b):
        self.body = b


class _Request:
    @staticmethod
    def requestWithIdentifier_content_trigger_(ident, content, trigger):
        return (ident, content, trigger)


class FakeCenter:
    def __init__(self, grant=True):
        self.grant, self.auth, self.added = grant, [], []

    def requestAuthorizationWithOptions_completionHandler_(self, opts, handler):
        self.auth.append(opts)
        handler(self.grant, None)

    def addNotificationRequest_withCompletionHandler_(self, request, handler):
        self.added.append(request)


@pytest.fixture()
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    un = types.ModuleType("UserNotifications")
    un.UNAuthorizationOptionAlert, un.UNAuthorizationOptionSound = 4, 2
    un.UNMutableNotificationContent, un.UNNotificationRequest = _Content, _Request
    monkeypatch.setitem(sys.modules, "UserNotifications", un)
    ran: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **_k: ran.append(list(cmd)))
    n.reset_for_tests()
    yield ran
    n.reset_for_tests()


def test_banner_goes_through_the_center(mac, monkeypatch):
    center = FakeCenter()
    monkeypatch.setattr(n, "_mac_center", lambda: center)
    assert n.mac_toast("Chat", "Finished.") is True
    assert n.mac_toast("Chat", "Again.") is True
    assert center.auth == [6]  # asked once, alert | sound
    (_id, content, trigger), _ = center.added
    assert (content.title, content.body, trigger) == ("Chat", "Finished.", None)
    assert mac == []


def test_unsigned_or_dev_app_uses_osascript(mac, monkeypatch):
    monkeypatch.setattr(n, "_mac_center", lambda: None)
    assert n.mac_toast('Say "hi"', "it's done") is True
    (cmd,) = mac
    assert cmd[0] == "osascript" and cmd[-2:] == ['Say "hi"', "it's done"]  # argv, never script text


def test_refused_permission_falls_back(mac, monkeypatch):
    center = FakeCenter(grant=False)
    monkeypatch.setattr(n, "_mac_center", lambda: center)
    n.mac_toast("a", "b")
    n.mac_toast("a", "c")
    assert len(center.added) == 1 and mac[0][-1] == "c"


def test_bind_desktop_uses_mac_toast(mac):
    n.bind_desktop(lambda *a: True, None)
    assert n._toast_fn is n.mac_toast
