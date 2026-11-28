"""The Account tab: who is signed in, signing in with a code, the account's PCs and agent
limits, and opening the site. Both backends ask a fake site on this PC from the same
login and must send it the same calls, give the same answer, tell the windows the same
and leave the same login and settings behind. Nothing here reaches the real site, and
no browser opens."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import pytest

from .parity_harness import plain

_MARK = {"marker": 1}
_SESSION = {"session_name": "duckyos_session_x", "session_value": "sess-1", "csrf_name": "duckyos_csrf_x", "csrf_value": "csrf-1"}
_DEVICE = {"device_key": "device-token-1", "device_key_id": "key-mine"}
_WHO = {"email": " Ducky@Example.com "}
_ME, _ACL = "/api/v1/auth/me", "/api/v1/acl/self"
_PCS = "/api/v1/plugins/uefn-ducky/collect/desktop-devices"
_REVOKE = "/api/v1/plugins/uefn-ducky/collect/desktop-device-revoke"
_START = "/api/v1/plugins/uefn-ducky/collect/desktop-device-start"
_POLL = "/api/v1/plugins/uefn-ducky/collect/desktop-device-poll"
_CAPS = "/api/v1/plugins/uefn-ducky/collect/agent-caps-get"
_SECRETS = ("device-token-1", "sess-1", "csrf-1", "sess-2", "csrf-2", "dky_v1_tok", "dev-secret")


def _comparable(answer: Any) -> Any:
    """Each backend words a failed connection its own way."""
    if isinstance(answer, dict) and str(answer.get("error") or "").startswith("Network error: "):
        return {**answer, "error": "Network error: ..."}
    return answer


@pytest.fixture
def acct(fake, rust_store, python, login, monkeypatch):
    """``acct.both(blob, method, *args)``: each backend's call from the same login row
    (none for an empty ``blob``) and default settings: the answer, the site's calls, the
    login row after, the events, and whether the data key's copy is still on this PC.
    ``acct.settings`` is the settings rows it left. ``acct.each(blob, run)`` is the same
    for any ``run(backend)``; ``arm()`` runs before each backend's turn, and the turn is
    over once ``settled()`` says so."""
    from backend.agent import secrets
    from backend.store.repos import settings
    from backend.store.repos.secrets import LOGIN_NAME
    from backend.uefn_plugins import data_crypto
    from frontend import duckyos_account
    from frontend.ui_web.agent_turns import push

    pushed: list[dict] = []
    monkeypatch.setattr(push, "_panel_push", pushed.append)  # never the live app's panel
    # The new account's data key is fetched on a thread of its own; not this call's work.
    monkeypatch.setattr(data_crypto, "prefetch", lambda: None)
    # A sign-in starts these in the Python backend only; none is the call's own work.
    for started in ("start_presence_heartbeat", "start_rpc_waiter", "publish_agent_catalog"):
        monkeypatch.setattr(duckyos_account, started, lambda: None)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *args, **kwargs: opened.append(url))  # never the person's browser
    cursor = [0]

    def rust_events() -> list[dict]:
        # A marker ends the page, so a call that pushed nothing does not wait out a poll.
        assert rust_store.call("push_appearance_live", _MARK) == (200, {"ok": True, "result": None})
        _, page = rust_store.request(f"/__panel_events?since={cursor[0]}")
        cursor[0] = page["cursor"]
        return page["events"][:-1]

    def forget() -> None:
        secrets.forget_login_stamp()
        duckyos_account.forget_account_key()

    class Account:
        site = fake
        browser = opened
        settings: dict = {}

        @staticmethod
        def each(blob: dict, run: Any, arm: Any = None, settled: Any = None) -> tuple[Any, list, dict, list, bool]:
            seen = []
            for backend in (python, rust_store):
                if blob:
                    login({"base_url": fake.url + "/", **blob})
                else:
                    secrets.clear_key(LOGIN_NAME)
                    forget()
                settings.replace_all({}, {})
                copy = data_crypto._cache_path()
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text("{}", encoding="utf-8")
                fake.calls.clear()
                pushed.clear()
                if arm:
                    arm()
                answer = run(backend)
                # Python's work after a sign-in ends in its own turn, not in the next one's login.
                for thread in threading.enumerate():
                    if thread.name == "ducky-login-followup":
                        thread.join(10)
                deadline = time.monotonic() + 10
                while settled and not settled() and time.monotonic() < deadline:
                    time.sleep(0.02)
                events = list(pushed) if backend is python else rust_events()
                forget()
                row = json.loads(secrets.get_key_fresh(LOGIN_NAME) or "{}")
                seen.append((_comparable(answer), list(fake.calls), row, events, copy.exists(), plain(settings.load_fields())))
            theirs, ours = seen
            for name, mine, yours in zip(("answer", "calls", "login row", "events", "data key copy", "settings"), ours, theirs):
                assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
            for secret in _SECRETS:
                assert secret not in json.dumps(theirs[0]) and secret not in json.dumps(theirs[3]), "a secret left the login row"
            Account.settings = theirs[5]
            return theirs[:5]

        @staticmethod
        def both(blob: dict, method: str, *args: Any, **how: Any) -> tuple[Any, list, dict, list, bool]:
            def run(backend: Any) -> Any:
                status, reply = plain(backend.call(method, *args))
                assert status == 200 and reply["ok"] is True, f"{method}{args}: {reply}"
                return reply["result"]

            return Account.each(blob, run, **how)

    return Account


def test_signed_out_and_a_paired_pc_answer_from_the_login_row(acct):
    status, calls, row, events, _ = acct.both({}, "duckyos_get_status")
    assert status["logged_in"] is False and status["account"] == "" and status["base_url"].startswith("https://127.0.0.1:")
    assert (calls, row, events) == ([], {}, [])

    blob = {
        **_DEVICE, **_WHO, "display_name": "Ducky", "user_id": 7, "roles": ["admin", 3], "permissions": ["store.publish"],
        "device_key_error": "old", "agent_denied": ["tool_a"], "agent_settings": {"a": 1},
    }  # fmt: skip
    status, calls, row, events, copy = acct.both(blob, "duckyos_get_status")
    assert status["logged_in"] is True and status["device_key_active"] is True and status["user_id"] == "7"
    assert status["account"] == f"{acct.site.url}|ducky@example.com" and status["base_url"] == acct.site.url + "/"
    assert (status["roles"], status["agent_denied"], status["agent_settings"]) == (["admin", 3], ["tool_a"], {"a": 1})
    assert calls == [] and events == [] and copy is True and row["device_key"] == "device-token-1"


_COOKIES = {"Set_Cookie": ["duckyos_session_x=sess-2; Path=/; HttpOnly", "other=1", "duckyos_csrf_x=csrf-2", "duckyos_login_challenge=ch-1"], "X_Ducky_Csrf_Token": "from-header"}
_ME_ANSWERS = [
    ({"email": "new@example.com", "userId": "u1", "displayName": "New", "roles": ["staff", 2]}, 200, _COOKIES),
    ({"user_id": 12, "display_name": "Alt", "roles": "admin"}, 200, {}),
    ({}, 200, {"X_Ducky_Csrf_Token": "only-a-header"}),
    ([1], 200, {}),
    ("<html>Sign in</html>", 200, {}),
    ({"error": "down"}, 500, _COOKIES),
    ({"error": "no"}, 403, {}),
    ({"error": "expired"}, 401, _COOKIES),
]


@pytest.mark.parametrize("paired", [False, True], ids=["session-only", "paired"])
@pytest.mark.parametrize("answer", _ME_ANSWERS, ids=[f"{status}-{n}" for n, (_, status, _) in enumerate(_ME_ANSWERS)])
def test_a_website_session_is_asked_who_it_is(acct, answer, paired):
    body, code, headers = answer
    acct.site.says(body, code, path=_ME, **headers)
    acct.site.says({"permissions": ["store.publish", 5]}, path=_ACL)
    blob = {**_SESSION, **_WHO, **(_DEVICE if paired else {})}
    status, calls, row, events, copy = acct.both(blob, "duckyos_get_status")
    assert calls[0]["method"] == "GET" and calls[0]["path"] == _ME
    assert calls[0]["headers"]["cookie"] == "duckyos_session_x=sess-1; duckyos_csrf_x=csrf-1" and "x-csrf-token" not in calls[0]["headers"]
    if code == 200 and isinstance(body, dict):
        assert [call["path"] for call in calls] == [_ME, _ACL] and status["permissions"] == ["store.publish", "5"] and "error" not in status
        # Another email is another account: the windows are told.
        assert row["permissions"] == ["store.publish", "5"] and bool(events) is ("email" in body)
    elif code == 401:
        # The dead session goes; what pairs this PC stays.
        assert len(calls) == 1 and "session_value" not in row and status["logged_in"] is paired
        assert status.get("session_expired", False) is (not paired) and copy is paired
        assert [event["type"] for event in events] == ([] if paired else ["duckyos_account_changed", "chats_changed"])
    else:
        assert len(calls) == 1 and status["logged_in"] is True and row["session_value"] == "sess-1" and events == []
        assert status["error"] == f"Could not load account (HTTP {code})"
    if headers is _COOKIES and code == 200:
        assert (row["session_value"], row["csrf_value"], row["challenge_value"]) == ("sess-2", "csrf-2", "ch-1")
        assert calls[1]["headers"]["cookie"] == "duckyos_session_x=sess-2; duckyos_csrf_x=csrf-2"


_ACL_ANSWERS = [({"permissions": "all"}, 200), ({"permissions": ["kept-out"]}, 403), ("oops", 200), ([], 200), ({"error": "expired"}, 401)]


@pytest.mark.parametrize("answer", _ACL_ANSWERS, ids=[f"{status}-{n}" for n, (_, status) in enumerate(_ACL_ANSWERS)])
def test_permissions_that_do_not_come_leave_the_old_ones(acct, answer):
    acct.site.says({"email": "ducky@example.com", "userId": "u1"}, path=_ME)
    acct.site.says(*answer, path=_ACL)
    status, calls, row, _, _ = acct.both({**_SESSION, **_WHO, "permissions": ["old"]}, "duckyos_get_status")
    assert len(calls) == 2 and status["permissions"] == ["old"] and row["user_id"] == "u1" and "error" not in status


def test_a_site_that_cannot_be_reached_answers_from_the_login_row(acct):
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        gone = f"http://127.0.0.1:{probe.getsockname()[1]}"
    blob = {"base_url": gone, **_SESSION, **_WHO, "roles": ["staff"]}
    status, calls, row, events, copy = acct.both(blob, "duckyos_get_status")
    assert status["logged_in"] is True and status["roles"] == ["staff"] and status["error"] == "Network error: ..."
    assert calls == [] and row == blob and events == [] and copy is True
    pcs, *_ = acct.both(blob, "duckyos_list_pcs")
    assert pcs == {"ok": False, "error": "Network error: ...", "code": "network", "devices": []}


_ROWS = [
    {"keyId": " key-mine ", "name": "", "live": 1, "last_seen": 1700000000.9},
    {"key_id": "other", "name": "Laptop", "live": 0, "last_seen": "12"},
    "junk",
    {"keyId": None, "name": 5},
    {},
]
_PC_ANSWERS = [
    ({"ok": True, "payload": {"devices": _ROWS}}, 200),
    ({"devices": _ROWS[:2]}, 200),
    ({"payload": {"devices": "none"}}, 200),
    ([1], 200),
    ({}, 404),
    ({"error": "down"}, 500),
    ({"error": "Nope"}, 403),
    ({"error": "this plugin is off"}, 403),
    ("<html>Wi-Fi sign-in</html>", 200),
]


@pytest.mark.parametrize("answer", _PC_ANSWERS, ids=[f"{status}-{n}" for n, (_, status) in enumerate(_PC_ANSWERS)])
def test_the_accounts_pcs_are_listed_the_same(acct, answer):
    acct.site.says(*answer)
    pcs, calls, row, events, copy = acct.both({**_DEVICE, **_WHO}, "duckyos_list_pcs")
    (call,) = calls
    assert call["path"] == _PCS and call["body"] == {} and call["headers"]["authorization"] == "Bearer device-token-1"
    assert row["device_key"] == "device-token-1" and events == [] and copy is True
    if answer[0] == {"ok": True, "payload": {"devices": _ROWS}}:
        assert pcs["devices"][0] == {"keyId": "key-mine", "name": "UEFN Ducky", "live": True, "last_seen": 1700000000, "mine": True}
        assert [(pc["keyId"], pc["name"], pc["last_seen"], pc["mine"]) for pc in pcs["devices"][1:]] == [("other", "Laptop", 12, False), ("", "5", 0, False), ("", "UEFN Ducky", 0, False)]
    elif not pcs["ok"]:
        assert pcs["devices"] == [] and pcs["code"] == ("rpc_unavailable" if answer[1] == 404 else "devices_failed")


def test_signed_out_has_no_pcs_and_asks_nobody(acct):
    pcs, calls, *_ = acct.both({}, "duckyos_list_pcs")
    assert pcs == {"ok": False, "error": "Not logged in", "code": "not_logged_in", "devices": []} and calls == []


@pytest.mark.parametrize("session", [False, True], ids=["key-only", "key-and-session"])
def test_a_device_key_the_site_refuses_is_forgotten(acct, session):
    acct.site.says({"error": "revoked"}, 401)
    blob = {**_DEVICE, **_WHO, **(_SESSION if session else {}), "device_key_error": "old"}
    pcs, calls, row, events, copy = acct.both(blob, "duckyos_list_pcs")
    assert (pcs["code"], pcs["error"], len(calls)) == ("device_unpaired", "This PC was disconnected", 1)
    assert not {"device_key", "device_key_id", "device_key_error"} & set(row) and copy is False
    # With a website session the person is still signed in, as the same account.
    assert ("session_value" in row) is session and bool(events) is (not session)
    if not session:
        assert events == [{"type": "duckyos_account_changed", "account": ""}, {"type": "chats_changed", "open": False}]


def test_a_session_the_site_refuses_is_signed_out(acct):
    acct.site.says({"error": "expired"}, 401)
    pcs, calls, row, events, copy = acct.both({**_SESSION, **_WHO}, "duckyos_list_pcs")
    assert (pcs["code"], len(calls), row, copy) == ("session_expired", 1, {}, False)
    assert calls[0]["headers"]["x-csrf-token"] == "csrf-1" and events[0] == {"type": "duckyos_account_changed", "account": ""}


def test_dropping_a_pc_asks_the_site_and_forgets_this_pcs_own_key(acct):
    for none in ("", "   ", None):
        refused, calls, *_ = acct.both({**_DEVICE, **_WHO}, "duckyos_revoke_pc", none)
        assert refused == {"ok": False, "error": "No PC selected", "code": "no_key", "logged_in": False} and calls == []

    blob = {**_DEVICE, **_SESSION, **_WHO}
    acct.site.says({"error": "gone already"}, 500, path="/api/v1/auth/api-keys/other")
    done, calls, row, events, copy = acct.both(blob, "duckyos_revoke_pc", " other ")
    website, plugin = calls
    assert (website["method"], website["path"], website["headers"]["x-csrf-token"]) == ("DELETE", "/api/v1/auth/api-keys/other", "csrf-1")
    assert plugin["path"] == _REVOKE and plugin["body"] == {"keyId": "other"} and plugin["headers"]["authorization"] == "Bearer device-token-1"
    assert done["ok"] is True and done["device_key_active"] is True and row["device_key_id"] == "key-mine" and events == [] and copy is True

    # This PC's own key: gone here too. The website session keeps the person signed in.
    done, calls, row, events, copy = acct.both(blob, "duckyos_revoke_pc", "key-mine")
    assert done["ok"] is True and done["logged_in"] is True and done["device_key_active"] is False and len(calls) == 2
    assert "device_key" not in row and row["session_value"] == "sess-1" and events == [] and copy is False
    # With no session it signs this PC out, and no website call is made.
    done, calls, row, events, _ = acct.both({**_DEVICE, **_WHO}, "duckyos_revoke_pc", "key-mine")
    assert done["ok"] is True and done["logged_in"] is False and [call["path"] for call in calls] == [_REVOKE]
    assert "device_key" not in row and [event["type"] for event in events] == ["duckyos_account_changed", "chats_changed"]

    acct.site.says({"error": "Not yours"}, 403)
    refused, calls, row, *_ = acct.both(blob, "duckyos_revoke_pc", "key-mine")
    assert refused == {"ok": False, "error": "Not yours", "code": "revoke_failed", "logged_in": False} and row["device_key"] == "device-token-1"


def test_a_site_that_hangs_holds_up_no_other_call(acct, login, rust_store):
    login({"base_url": acct.site.url, **_SESSION, **_WHO})
    acct.site.says({"email": "ducky@example.com"}, path=_ME)
    acct.site.hold.clear()
    answers: list[Any] = []
    waiting = threading.Thread(target=lambda: answers.append(rust_store.call("duckyos_get_status")), daemon=True)
    waiting.start()
    deadline = time.monotonic() + 10
    while not acct.site.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert acct.site.calls, "the site was never asked"
    started = time.monotonic()
    for _ in range(20):
        status, reply = rust_store.call("get_version")
        assert status == 200 and reply["ok"] is True
    assert time.monotonic() - started < 5 and not answers
    acct.site.hold.set()
    waiting.join(20)
    assert answers and answers[0][1]["result"]["logged_in"] is True


# --------------------------------------------------------------------------- signing in

_CODES = {"payload": {"user_code": " ABCD-1234 ", "device_code": " dev-secret "}}
_APPROVED = {"payload": {"token": "dky_v1_tok", "keyId": "key-9", "email": "New@Example.com"}}
_REFUSED = {"ok": False, "logged_in": False}


def _secure(acct) -> str:
    """The address a sign-in is for: the site's, which is always https."""
    return acct.site.url.replace("http://", "https://")


@pytest.mark.parametrize("prior", [_SESSION, _DEVICE], ids=["session", "paired"])
def test_signing_in_shows_a_code_and_pairs_this_pc(acct, prior):
    def arm() -> None:
        acct.site.says(_CODES, path=_START)
        acct.site.says_in_turn(_POLL, {"status": "pending"}, _APPROVED)

    status, calls, row, events, copy = acct.both({**prior, **_WHO}, "duckyos_login", arm=arm)
    secure = _secure(acct)
    assert status["ok"] is True and status["logged_in"] is True and status["device_key_active"] is True and status["email"] == "New@Example.com"
    # The answer is made while the code still shows.
    assert (status["browser_pending"], status["user_code"]) == (True, "ABCD-1234")
    assert [call["path"] for call in calls] == [_START, _POLL, _POLL]
    assert calls[0]["body"] == {} and calls[1]["body"] == calls[2]["body"] == {"device_code": "dev-secret"}
    # The new login is the device key alone: the old session is not carried over.
    assert row == {"base_url": secure, "email": "New@Example.com", "device_key": "dky_v1_tok", "device_key_id": "key-9"}
    assert events[0] == {"type": "duckyos_account_changed", "account": f"{secure}|new@example.com"} and copy is False
    assert acct.settings["duckyos_base_url"] == secure and acct.settings["remote_access"] is True
    # Nothing more is asked of a backend here: each fetches the account's agent limits on
    # its own after a sign-in, from whatever login it finds then.


_NOT_STARTED = [
    (({"error": "Sign-ins are closed"}, 403), 1, "device_start_failed", "Sign-ins are closed"),
    (({}, 404), 3, "auth_unavailable", "uefnducky.org's sign-in isn't answering (HTTP 404). Try again in a minute."),
    (({"payload": {"user_code": "ABCD"}}, 200), 1, "device_start_failed", "Could not start login."),
]


@pytest.mark.parametrize("case", _NOT_STARTED, ids=["refused", "not-there-after-3-tries", "no-device-code"])
def test_a_sign_in_the_site_does_not_start_changes_no_login(acct, case):
    answer, tries, code, error = case
    acct.site.says(*answer, path=_START)
    blob = {**_DEVICE, **_WHO}
    refused, calls, row, events, copy = acct.both(blob, "duckyos_login")
    assert refused == {**_REFUSED, "error": error, "code": code}
    assert [call["path"] for call in calls] == [_START] * tries and calls[0]["headers"]["authorization"] == "Bearer device-token-1"
    assert row == {"base_url": acct.site.url + "/", **blob} and events == [] and copy is True


def test_a_code_nobody_approved_is_no_sign_in(acct):
    acct.site.says(_CODES, path=_START)
    acct.site.says({"payload": {"status": "denied", "token": "not-a-key"}}, path=_POLL)
    refused, calls, row, events, _ = acct.both({**_DEVICE, **_WHO}, "duckyos_login")
    assert refused == {**_REFUSED, "error": "Timed out waiting for the code to be approved.", "code": "browser_timeout"}
    assert [call["path"] for call in calls] == [_START, _POLL] and row["device_key"] == "device-token-1" and events == []
    # Over: the code is gone.
    after, *_ = acct.both({**_DEVICE, **_WHO}, "duckyos_get_status")
    assert (after["browser_pending"], after["user_code"]) == (False, "")


def test_signed_out_a_sign_in_asks_the_sites_address_and_keeps_it(acct):
    # The fake site does not speak https: three tries, and no call arrives.
    refused, calls, row, events, _ = acct.both({}, "duckyos_login", "")
    assert refused == {**_REFUSED, "error": "Network error: ...", "code": "network"} and (calls, row, events) == ([], {}, [])
    assert acct.settings["duckyos_base_url"] == _secure(acct) and "remote_access" not in acct.settings

    bad, calls, *_ = acct.both({}, "duckyos_login", "http://example.org")
    assert bad == {**_REFUSED, "error": "Account URL must use https://", "code": "invalid_url"} and calls == []
    assert "duckyos_base_url" not in acct.settings


def test_a_waiting_sign_in_shows_its_code_to_a_second_one_and_can_be_cancelled(acct):
    acct.site.says(_CODES, path=_START)
    acct.site.says({"status": "pending"}, path=_POLL)

    def run(backend: Any) -> tuple:
        def call(method: str) -> Any:
            return plain(backend.call(method))[1]["result"]

        first: list = []
        waiting = threading.Thread(target=lambda: first.append(call("duckyos_login")), daemon=True)
        waiting.start()
        deadline = time.monotonic() + 10
        while not (shown := call("duckyos_get_status"))["browser_pending"] and time.monotonic() < deadline:
            time.sleep(0.02)
        second, stopped = call("duckyos_login"), call("duckyos_cancel_login")
        waiting.join(15)
        del acct.site.calls[2:]  # how many polls fit before the cancel is timing
        return shown, second, stopped, first

    (shown, second, stopped, first), calls, row, events, _ = acct.each({**_DEVICE, **_WHO}, run)
    assert shown["user_code"] == second["user_code"] == "ABCD-1234" and second["ok"] is True and second["browser_pending"] is True
    assert stopped["browser_pending"] is False and stopped["logged_in"] is True
    assert first == [{**_REFUSED, "error": "Browser login cancelled", "code": "cancelled"}]
    assert [call["path"] for call in calls] == [_START, _POLL] and row["device_key"] == "device-token-1" and events == []


# --------------------------------------------------------------------------- agent limits

_LIMITS = {"payload": {"denied": ["tool_a", "", 5, " "], "settings": {"allow_agent_clicks": 1, "allow_see_uefn": 0, "junk": 1}}}
_CAPS_CASES = [
    (_DEVICE, (_LIMITS, 200)),
    ({**_DEVICE, "agent_denied": ["old"], "agent_settings": {"allow_see_other_programs": 0, "x": 1}}, ({"denied": "all", "settings": []}, 200)),
    (_DEVICE, ({"error": "down"}, 500)),
    (_DEVICE, ({"error": "revoked"}, 401)),
    ({**_DEVICE, "agent_caps_local": True}, (_LIMITS, 200)),
    (_SESSION, (_LIMITS, 200)),
    ({}, (_LIMITS, 200)),
]


@pytest.mark.parametrize("case", _CAPS_CASES, ids=["set", "kept", "site-down", "key-refused", "set-on-this-pc", "session", "signed-out"])
def test_the_accounts_agent_limits_are_fetched_the_same(acct, python, rust_store, case):
    from frontend import duckyos_account

    blob, answer = case
    acct.site.says(*answer, path=_CAPS)

    def run(backend: Any) -> None:
        if backend is python:
            duckyos_account.fetch_agent_caps()
        else:
            assert rust_store.request("/__rs/account/agent_caps", {}) == (200, {"ok": True})

    _, calls, row, events, _ = acct.each({**blob, **_WHO} if blob else {}, run)
    asked = bool(blob) and not blob.get("agent_caps_local")
    assert [call["path"] for call in calls] == ([_CAPS] if asked else [])
    if answer[1] == 401:
        # The key is dead: this PC is signed out, and the windows are told.
        assert "device_key" not in row and events[0] == {"type": "duckyos_account_changed", "account": ""}
    elif blob is _DEVICE and answer[1] == 200:
        limits = {"allow_settings_write": True, "allow_agent_clicks": True, "allow_see_uefn": False, "allow_see_other_programs": True}
        assert row["agent_denied"] == ["tool_a"] and row["agent_settings"] == limits
        assert acct.settings["allow_agent_clicks"] is True and acct.settings["allow_see_uefn"] is False
    elif "agent_settings" in blob:
        # Nothing usable came: the login keeps its limits, and the settings follow them.
        assert row["agent_denied"] == ["old"] and acct.settings["allow_see_other_programs"] is False
    elif blob.get("agent_caps_local") or answer[1] == 500:
        assert "agent_settings" not in row and "allow_agent_clicks" not in acct.settings


# --------------------------------------------------------------------------- the site's pages


def test_a_page_of_the_site_opens_at_the_accounts_address(acct):
    paired = {**_DEVICE, **_WHO}
    pages = [({}, (), _secure(acct) + "/"), (paired, ("store/x",), "/store/x"), (paired, ("/a?b=1#c",), "/a?b=1#c"), (paired, (None,), "/"), (paired, (5,), "/5")]
    for blob, args, page in pages:
        url = page if page.startswith("https://") else acct.site.url + page
        acct.browser.clear()
        answer, calls, _, events, _ = acct.both(blob, "duckyos_open_site", *args)
        assert answer == {"ok": True, "url": url} and (calls, events) == ([], [])
        # The Python backend asked for the browser once; under the tests the Rust one opens nothing.
        assert acct.browser == [url]
