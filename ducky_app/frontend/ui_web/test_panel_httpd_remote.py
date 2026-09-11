"""Cookie gate for the remote panel HTTP server."""

from __future__ import annotations

import time

import pytest

from frontend.ui_web import panel_httpd as httpd


@pytest.fixture
def remote_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(httpd, "default_app_data_dir", lambda: tmp_path)
    httpd._cookie_secret = None
    httpd._one_time.clear()
    httpd._sessions.clear()
    yield
    httpd._cookie_secret = None
    httpd._one_time.clear()
    httpd._sessions.clear()


def test_loopback_host_is_open(remote_auth):
    assert httpd.host_is_local("127.0.0.1:4199")
    assert httpd.request_is_authorized("127.0.0.1:4199", "/", None)
    assert httpd.request_is_authorized("127.0.0.1:4199", "/assets/x.js", None)
    assert httpd.request_is_authorized("localhost:4199", "/__panel_api/x", None)


def test_remote_host_without_cookie_is_403(remote_auth):
    host = "u-abc.app.uefnducky.org"
    assert not httpd.request_is_authorized(host, "/", None)
    assert not httpd.request_is_authorized(host, "/assets/app.js", None)
    assert not httpd.request_is_authorized(host, "/__panel_api/list_conversations", None)


def test_login_token_single_use_and_expiry(remote_auth, monkeypatch):
    token = httpd.mint_remote_login_token()
    assert httpd.consume_remote_login_token(token)
    assert not httpd.consume_remote_login_token(token)

    token2 = httpd.mint_remote_login_token()
    real_time = time.time
    monkeypatch.setattr(httpd.time, "time", lambda: real_time() + 1000)
    assert not httpd.consume_remote_login_token(token2)


def test_cookie_bound_to_host(remote_auth):
    host = "u-abc.app.uefnducky.org"
    other = "u-other.app.uefnducky.org"
    cookie = httpd.issue_remote_cookie(host)
    header = f"{httpd._COOKIE_NAME}={cookie}"
    assert httpd.request_is_authorized(host, "/", header)
    assert not httpd.request_is_authorized(other, "/", header)
    assert httpd.remote_session_count() >= 1
    httpd.sign_out_all_remote()
    assert not httpd.request_is_authorized(host, "/", header)


def test_local_bridge_paths_stay_loopback(remote_auth):
    host = "u-abc.app.uefnducky.org"
    cookie = httpd.issue_remote_cookie(host)
    header = f"{httpd._COOKIE_NAME}={cookie}"
    assert not httpd.request_is_authorized(host, "/__panel_run", header)
    assert httpd.request_is_authorized("127.0.0.1:4199", "/__panel_run", None)
