"""Feedback collect payload — origin, redact, truncate, skip empty log."""

from __future__ import annotations

from unittest.mock import patch

from backend.util.http import HttpError
from frontend.feedback_submit import (
    COLLECT_URL,
    ERROR_LOG_MAX_BYTES,
    FORM_ID,
    ORIGIN,
    build_payload,
    submit_feedback,
)


def test_build_payload_skips_empty_log() -> None:
    body = build_payload(message="  hello  ", email="", error_log="", app_version="1.2.3")
    assert body["formId"] == FORM_ID
    assert body["message"] == "hello"
    assert body["source"] == "desktop"
    assert body["app_version"] == "1.2.3"
    assert body["_hp"] == ""
    assert "email" not in body
    assert "error_log" not in body


def test_build_payload_redacts_and_truncates() -> None:
    log = "Authorization Bearer sk-abc123456789 " + ("x" * 9000)
    body = build_payload(message="hi", error_log=log)
    blob = body["error_log"]
    assert "[redacted]" in blob
    assert "sk-abc123456789" not in blob
    assert len(blob.encode("utf-8")) <= ERROR_LOG_MAX_BYTES


def test_submit_sends_origin_and_form_id() -> None:
    captured: dict = {}

    def fake_http_json(method, url, *, headers=None, json_body=None, timeout=60):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json_body"] = json_body
        captured["timeout"] = timeout
        return {"ok": True, "payload": {"stored": True}}

    with patch("frontend.feedback_submit.http_json", fake_http_json):
        out = submit_feedback(message="ship faster", email="a@b.co", include_errors=False)
    assert out == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == COLLECT_URL
    assert captured["headers"]["Origin"] == ORIGIN
    assert captured["json_body"]["formId"] == FORM_ID
    assert captured["json_body"]["message"] == "ship faster"
    assert captured["json_body"]["email"] == "a@b.co"
    assert "error_log" not in captured["json_body"]


def test_submit_attaches_errors_when_asked() -> None:
    captured: dict = {}

    def fake_http_json(method, url, *, headers=None, json_body=None, timeout=60):
        captured["json_body"] = json_body
        return {"ok": True}

    with (
        patch("frontend.feedback_submit.http_json", fake_http_json),
        patch("frontend.feedback_submit._current_error_log", return_value="[boom] (panel) failed"),
    ):
        out = submit_feedback(message="broken", include_errors=True)
    assert out["ok"] is True
    assert captured["json_body"]["error_log"] == "[boom] (panel) failed"


def test_submit_skips_empty_error_log() -> None:
    captured: dict = {}

    def fake_http_json(method, url, *, headers=None, json_body=None, timeout=60):
        captured["json_body"] = json_body
        return {"ok": True}

    with (
        patch("frontend.feedback_submit.http_json", fake_http_json),
        patch("frontend.feedback_submit._current_error_log", return_value="  "),
    ):
        submit_feedback(message="hi", include_errors=True)
    assert "error_log" not in captured["json_body"]


def test_submit_maps_http_error() -> None:
    with patch(
        "frontend.feedback_submit.http_json",
        side_effect=HttpError("HTTP 403", status=403, detail={}),
    ):
        out = submit_feedback(message="hi")
    assert out["ok"] is False
    assert "blocked" in out["error"]


def test_build_payload_carries_the_app_log_scrubbed() -> None:
    body = build_payload(
        message="slow chat",
        app_log="[2026-10-09 10:00:00] (panel) signed in as someone@example.com from 203.0.113.7",
        app_version="1.2.358",
    )
    assert "app_log" in body and "error_log" not in body
    assert "someone@example.com" not in body["app_log"] and "203.0.113.7" not in body["app_log"]
    assert "[email]" in body["app_log"] and "[ip]" in body["app_log"]


def test_submit_feedback_sends_the_logs_that_were_asked_for() -> None:
    sent: dict = {}

    def fake_post(method, url, *, headers, json_body, timeout):
        sent.update(json_body)
        return {}

    with (
        patch("frontend.feedback_submit.http_json", side_effect=fake_post),
        patch("frontend.feedback_submit._current_error_log", return_value="[t] (agent) boom"),
        patch("frontend.feedback_submit._current_app_log", return_value="[t] (panel) started"),
    ):
        assert submit_feedback(message="hi", include_log=True)["ok"] is True
    assert sent.get("app_log") == "[t] (panel) started" and "error_log" not in sent
