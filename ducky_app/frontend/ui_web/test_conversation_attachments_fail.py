"""An image attachment that fails to save is logged and named; good ones are unchanged."""

from __future__ import annotations

import base64
import logging

from backend.agent.message_attachment import MessageAttachment
from frontend.ui_web import conversation_attachments as ca

_PNG = base64.b64encode(b"\x89PNG\r\n").decode()


def _img(name: str, data: str = _PNG) -> MessageAttachment:
    return MessageAttachment(kind="image", name=name, mime="image/png", data_base64=data)


def test_a_failed_attachment_is_named_in_the_result(tmp_path, caplog) -> None:
    shots = [_img("shot-1.png"), _img("shot-2.png"), _img("shot-3.png", "not base64!!")]
    with caplog.at_level(logging.WARNING, logger=ca.__name__):
        rows = ca.persist_message_attachments("conv1", 1.0, shots, tmp_path)
    assert [r["name"] for r in rows] == ["shot-1.png", "shot-2.png"]
    assert rows.failed == ["shot-3.png"]
    assert ca.failed_attachments_line(rows) == "Could not attach: shot-3.png"
    assert any(r.exc_info for r in caplog.records if r.name == ca.__name__)


def test_good_attachments_are_unchanged(tmp_path) -> None:
    rows = ca.persist_message_attachments("conv1", 1.5, [_img("a.png")], tmp_path)
    assert rows == [{"kind": "image", "name": "a.png", "mime": "image/png", "path": "attachments/1500_0_a.png"}]
    assert rows.failed == [] and ca.failed_attachments_line(rows) == ""
    assert (tmp_path / "conv1" / rows[0]["path"]).read_bytes() == b"\x89PNG\r\n"
    assert ca.persist_message_attachments("conv1", 1.5, [], tmp_path) == []
