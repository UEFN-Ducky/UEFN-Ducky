"""ducky_generate_image: a chat makes a picture on the person's default maker and sees it."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from backend.automations import media
from backend.tools.core import workspace_code
from frontend.ui_web import generated_images as gen

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def maker(tmp_path: Path, monkeypatch) -> list[dict[str, Any]]:
    (tmp_path / "gen").mkdir()
    monkeypatch.setattr(gen, "generated_images_dir", lambda for_write=False: tmp_path / "gen")
    monkeypatch.setattr(gen, "_chat_of_caller", lambda: "c1")
    monkeypatch.setattr(media, "default_provider", lambda ntype: "")
    calls: list[dict[str, Any]] = []

    def draw(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        out = Path(kwargs["output_dir"]) / "duck.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_PNG)
        return {"downloaded": [str(out)]}

    rows = [{"id": "ducky_ai_image", "label": "Ducky AI · Image", "plugin": "Ducky AI", "plugin_id": "account",
             "tool": "ducky_ai_image", "credits": 0, "cost": "Ducky AI credit", "paid": True,
             "args": lambda i, c: {"prompt": i["prompt"]}}]
    monkeypatch.setattr(media, "table", lambda ntype: rows)
    monkeypatch.setattr(media, "tool_fn", lambda name: draw)
    return calls


def test_it_asks_before_spending_then_keeps_the_picture_for_the_chat_once(maker) -> None:
    with pytest.raises(ValueError, match="confirm_spend=true"):
        workspace_code.ducky_generate_image("a duck")
    assert maker == []
    out = workspace_code.ducky_generate_image("a duck", confirm_spend=True, backend="ducky_ai_image")
    from mcp.server.fastmcp import Image

    assert isinstance(out[1], Image) and json.loads(out[0])["backend"] == "Ducky AI · Image"
    assert maker[0]["prompt"] == "a duck" and maker[0]["confirm_spend"] is True
    kept = [r for r in gen.list_generated_images() if r.get("conv_id") == "c1"]
    assert len(kept) == 1 and Path(kept[0]["path"]).read_bytes() == _PNG


def test_a_picture_the_plugin_already_kept_is_not_kept_again(maker, monkeypatch) -> None:
    real = media.tool_fn("ducky_ai_image")

    def kept_by_plugin(**kwargs: Any) -> dict[str, Any]:
        data = real(**kwargs)
        gen.save_generated_image(_PNG, conv_id="c1", filename="from_plugin.png")
        return data

    monkeypatch.setattr(media, "tool_fn", lambda name: kept_by_plugin)
    workspace_code.ducky_generate_image("a duck", confirm_spend=True, backend="ducky_ai_image")
    assert [r["name"] for r in gen.list_generated_images()] == ["from_plugin.png"]


def test_a_yes_without_naming_the_maker_charges_nothing(maker) -> None:
    """The person said yes to one maker; a bare yes must not charge the default instead."""
    with pytest.raises(ValueError, match='Nothing was charged.*backend="ducky_ai_image"'):
        workspace_code.ducky_generate_image("a duck", confirm_spend=True)
    assert maker == []


def test_a_plugin_id_names_its_maker_and_a_wrong_one_lists_the_real_ones(maker, monkeypatch) -> None:
    assert media.pick_backend("image.generate", "account")["id"] == "ducky_ai_image"
    with pytest.raises(ValueError, match=r"Image backends here: ducky_ai_image \("):
        workspace_code.ducky_generate_image("a duck", confirm_spend=True, backend="openai")
    assert maker == []


def _own_key_maker(monkeypatch, tmp_path: Path, *, key: bool) -> list[dict[str, Any]]:
    """An AI gateway's own picture node, paid by the person's API key."""
    asked: list[dict[str, Any]] = []

    def node(ctx: dict[str, Any]) -> dict[str, Any]:
        asked.append(ctx["config"])
        out = tmp_path / "from-gateway.png"
        out.write_bytes(_PNG)
        return {"ok": True, "path": str(out)}

    rows = [{"id": "openai_image", "label": "OpenAI image", "plugin": "OpenAI", "plugin_id": "openai",
             "node": "openai.image", "key": "openai", "model": "gpt-image-2", "config_fields": [],
             "credits": 0, "paid": True, "args": lambda i, c: {"prompt": i["prompt"]}}]
    monkeypatch.setattr(media, "table", lambda ntype: rows)
    monkeypatch.setattr(media, "node_fn", lambda name: node)
    monkeypatch.setattr(media, "_has_key", lambda provider: key)
    return asked


def test_a_maker_on_the_persons_own_key_just_runs(maker, monkeypatch, tmp_path) -> None:
    """Asking for the picture was the yes: no billing question on their own API key."""
    asked = _own_key_maker(monkeypatch, tmp_path, key=True)
    out = workspace_code.ducky_generate_image("a duck")
    assert json.loads(out[0])["backend"] == "OpenAI image" and json.loads(out[0])["cost"] == "Your own API key"
    assert asked[0]["prompt"] == "a duck" and maker == []


def test_no_maker_set_up_says_what_to_set_up_and_names_no_bill(maker, monkeypatch, tmp_path) -> None:
    _own_key_maker(monkeypatch, tmp_path, key=False)
    monkeypatch.setattr(media, "why_not", lambda row, label="": "Add your OpenAI API key in Settings.")
    with pytest.raises(ValueError) as err:
        workspace_code.ducky_generate_image("a duck", confirm_spend=True)
    assert str(err.value) == "No picture maker is set up here yet. Add your OpenAI API key in Settings."


def test_a_paid_maker_this_account_does_not_have_is_nobodys_default(monkeypatch) -> None:
    """Its plugin lists the tool only for accounts with the service (api.tool(visible=)): the
    row plugin.json declares is then no picture maker here, however early it was loaded."""
    from backend.uefn_plugins.plugin_host import tool_registry, ui_registry

    rows = [{"id": "paid_image", "tool": "paid_image", "plugin_id": "account", "label": "Paid"},
            {"id": "own_image", "tool": "own_image", "plugin_id": "meshy", "label": "Own"}]
    monkeypatch.setattr(ui_registry, "image_generators", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(tool_registry, "plugin_tool_hidden", lambda name: name == "paid_image")
    assert [r["id"] for r in media.plugin_image_generators()] == ["own_image"]
    monkeypatch.setattr(tool_registry, "plugin_tool_hidden", lambda name: False)
    assert [r["id"] for r in media.plugin_image_generators()] == ["paid_image", "own_image"]
