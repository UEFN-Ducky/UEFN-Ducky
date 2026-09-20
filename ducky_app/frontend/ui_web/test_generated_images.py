"""Durable generated_images store — not the screenshot prune bucket."""

from pathlib import Path

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01"
    b"\xdd\x8d\xb4\x1c\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_save_list_and_attach(tmp_path, monkeypatch):
    from frontend.ui_web import generated_images as gi

    monkeypatch.setattr(gi, "generated_images_dir", lambda for_write=False: tmp_path)
    row = gi.save_generated_image(_PNG, prompt="a waffle", gateway="openai", model="gpt-image-1")
    assert row["ok"] is True
    assert Path(row["path"]).is_file()
    listed = gi.list_generated_images()
    assert listed and listed[0]["prompt"] == "a waffle"
    att = gi.attachment_from_path(row["path"])
    assert att and att["kind"] == "image" and att["data_base64"]
    tool_atts = gi.attachments_from_tool_result(
        "meshy_text_to_image",
        {"path": row["path"], "prompt": "mesh"},
    )
    assert tool_atts and tool_atts[0]["kind"] == "image"
    assert gi.attachments_from_tool_result("spawn_actor", {"path": row["path"]}) == []
