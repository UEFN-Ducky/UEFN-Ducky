"""A plugin tool's result passes the host's after-call checks, whoever called it.

The load / re-entrancy tests moved next to their code: plugin_host/test_boot.py,
test_loader.py and test_lifecycle.py.
"""

from __future__ import annotations


def test_a_plugin_tools_result_is_offered_to_the_chat_pictures(monkeypatch) -> None:
    """Every plugin tool's result passes the inline-pictures check, whoever called it."""
    import backend.uefn_plugins.host as host
    from frontend.ui_web import generated_images

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(host, "uefn_agent_tools_allowed", lambda pid: True)
    monkeypatch.setattr(generated_images, "note_tool_images", lambda name, result: seen.append((name, result)))
    wrapper = host._PluginApi("imgprobe").tool(name="imgprobe_make_image")(lambda: '{"downloaded": []}')
    try:
        assert wrapper() == '{"downloaded": []}'
        assert seen == [("imgprobe_make_image", '{"downloaded": []}')]
    finally:
        with host._LOCK:
            host._unregister_plugin_mcp_tools({"imgprobe_make_image"})
        host.invalidate_plugin_runtime("imgprobe")
