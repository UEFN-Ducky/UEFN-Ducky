"""Test that uploaded images are resolved to real file paths for coding agents."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from backend.agent.attachments import prepare_outgoing_user_message
from backend.agent.coding_agents.runner import collect_image_paths

# 1x1 transparent PNG.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9"
    "awAAAABJRU5ErkJggg=="
)


def test_external_agent_skips_vision_gate(monkeypatch):
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.copy_png_to_ducky_captures",
        lambda *a, **k: "",
    )
    # Embedded model unknown/non-vision would normally raise; external must not.
    content, stored = prepare_outgoing_user_message(
        "look at this",
        [{"kind": "image", "name": "shot.png", "mime": "image/png", "data_base64": _PNG_B64}],
        provider="anthropic",
        model="definitely-not-a-real-model",
        external_agent=True,
    )
    assert content == "look at this"
    assert stored[0]["kind"] == "image"


def test_image_only_message_has_empty_text_content(monkeypatch):
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.copy_png_to_ducky_captures",
        lambda *a, **k: "",
    )
    content, stored = prepare_outgoing_user_message(
        "",
        [{"kind": "image", "name": "shot.png", "mime": "image/png", "data_base64": _PNG_B64}],
        provider="anthropic",
        model="definitely-not-a-real-model",
        external_agent=True,
    )
    assert content == ""
    assert len(stored) == 1
    assert stored[0]["kind"] == "image"


def test_prepare_outgoing_adds_project_capture_path(monkeypatch, tmp_path):
    dest = tmp_path / "Saved" / "DuckyCaptures" / "shot.png"
    dest.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "frontend.ui_web.tool_captures.copy_png_to_ducky_captures",
        lambda *a, **k: str(dest),
    )
    content, stored = prepare_outgoing_user_message(
        "look",
        [{"kind": "image", "name": "shot.png", "mime": "image/png", "data_base64": _PNG_B64}],
        provider="anthropic",
        model="definitely-not-a-real-model",
        external_agent=True,
    )
    assert "Capture file:" in content
    assert stored[0]["project_path"] == str(dest)


def _model_windows(monkeypatch, windows: dict[str, int]) -> None:
    from backend.agent.model_fetch import ModelInfo

    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info",
        lambda p, m: ModelInfo(id=m, context_limit=windows[m]) if m in windows else None,
    )
    monkeypatch.setattr("frontend.ui_web.context_tokens.count_tokens", lambda text, model="", provider="": len(text) // 4)


def test_big_file_fits_a_big_model(monkeypatch):
    _model_windows(monkeypatch, {"big-1": 1_000_000})
    kit = "<html>" + "x" * 1_200_000 + "</html>"  # far past the old 256 KB cap
    content, stored = prepare_outgoing_user_message(
        "check this",
        [{"kind": "file", "name": "Island Media Kit.html", "mime": "text/html", "text": kit}],
        provider="openai", model="big-1", external_agent=True, coding_agent="codex",
    )
    assert kit in content
    assert stored[0]["name"] == "Island Media Kit.html"


def test_file_over_the_models_window_names_the_model(monkeypatch):
    import pytest

    _model_windows(monkeypatch, {"small-1": 8_000})
    with pytest.raises(ValueError, match=r"big\.html is about 10,000 tokens\. small-1 holds 8,000 tokens"):
        prepare_outgoing_user_message(
            "check this",
            [{"kind": "file", "name": "big.html", "mime": "text/html", "text": "x" * 40_000}],
            provider="openai", model="small-1",
        )


def test_unknown_window_leaves_the_size_to_the_api(monkeypatch):
    _model_windows(monkeypatch, {})
    content, _ = prepare_outgoing_user_message(
        "",
        [{"kind": "file", "name": "huge.txt", "mime": "text/plain", "text": "y" * 2_000_000}],
        provider="openai", model="mystery-model", external_agent=True, coding_agent="codex",
    )
    assert "Attached file: huge.txt" in content


def test_collect_image_paths_resolves_persisted_file(tmp_path, monkeypatch):
    conv_id = "conv-img-1"
    att_dir = tmp_path / conv_id / "attachments"
    att_dir.mkdir(parents=True)
    img = att_dir / "shot.png"
    img.write_bytes(base64.b64decode(_PNG_B64))

    import frontend.ui_web.project_chats as project_chats

    monkeypatch.setattr(project_chats, "get_conversations_dir", lambda root=None: tmp_path)

    conv = SimpleNamespace(
        id=conv_id,
        messages=[
            {"role": "user", "content": "old"},
            {
                "role": "user",
                "content": "look",
                "attachments": [
                    {"kind": "image", "name": "shot.png", "mime": "image/png", "path": "attachments/shot.png"},
                    {"kind": "file", "name": "note.txt", "path": "attachments/note.txt"},
                ],
            },
        ],
    )
    paths = collect_image_paths(conv)
    assert len(paths) == 1
    assert paths[0].endswith("shot.png")
    assert img.samefile(paths[0])


def test_collect_image_paths_none_when_no_attachments():
    conv = SimpleNamespace(id="c", messages=[{"role": "user", "content": "hi"}])
    assert collect_image_paths(conv) == []
