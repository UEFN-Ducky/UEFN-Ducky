"""Desktop-plugin prompt block must surface live Blender readiness."""

from __future__ import annotations

from backend.agent.toolsets import desktop_plugins as dp


def test_desktop_plugins_block_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.uefn_agent_tool_rows",
        lambda: [
            {
                "id": "blender",
                "label": "Blender",
                "tool_names": [
                    "blender_status",
                    "blender_execute_blender_code",
                    "blender_get_scene_info",
                ],
            }
        ],
    )
    monkeypatch.setattr(dp, "_tcp_open", lambda host, port, timeout=0.35: True)

    text = dp.enabled_desktop_plugins_prompt_block()
    assert "Enabled Store desktop plugins" in text
    assert "blender" in text.lower()
    assert "**READY**" in text
    assert "UEFN" in text
    assert "Blender or UEFN" in text
    assert "blender_execute_blender_code" in text


def test_desktop_plugins_block_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.uefn_plugins.host.uefn_agent_tool_rows",
        lambda: [{"id": "blender", "label": "Blender", "tool_names": ["blender_status"]}],
    )
    monkeypatch.setattr(dp, "_tcp_open", lambda host, port, timeout=0.35: False)

    text = dp.enabled_desktop_plugins_prompt_block()
    assert "**NOT READY**" in text
    assert "Do **not** blame UEFN" in text


def test_desktop_plugins_block_empty(monkeypatch) -> None:
    monkeypatch.setattr("backend.uefn_plugins.host.uefn_agent_tool_rows", lambda: [])
    assert dp.enabled_desktop_plugins_prompt_block() == ""
