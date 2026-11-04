"""The register(api) surface: a plugin tool's result passes the host's after-call checks."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host import api, lifecycle, state, tool_registry


def test_a_plugin_tools_result_is_offered_to_the_chat_pictures(monkeypatch) -> None:
    """Every plugin tool's result passes the inline-pictures check, whoever called it."""
    from frontend.ui_web import generated_images

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(api, "uefn_agent_tools_allowed", lambda pid: True)
    monkeypatch.setattr(generated_images, "note_tool_images", lambda name, result: seen.append((name, result)))
    wrapper = api._PluginApi("imgprobe").tool(name="imgprobe_make_image")(lambda: '{"downloaded": []}')
    try:
        assert wrapper() == '{"downloaded": []}'
        assert seen == [("imgprobe_make_image", '{"downloaded": []}')]
    finally:
        with state._LOCK:
            tool_registry._unregister_plugin_mcp_tools({"imgprobe_make_image"})
        lifecycle.invalidate_plugin_runtime("imgprobe")


def test_chat_reference_drops_a_bad_href() -> None:
    probe = api._PluginApi("refprobe")
    try:
        assert probe.chat_reference(trigger="@", id="x", label="X", href="javascript:alert(1)") is False
        assert probe.chat_reference(trigger="@", id="x", label="X", href="plugin:refprobe/main") is True
        rows = [r for r in state._CONTRIBUTIONS["chat_references"] if r.get("plugin_id") == "refprobe"]
        assert [r["href"] for r in rows] == ["plugin:refprobe/main"]
    finally:
        with state._LOCK:
            state._CONTRIBUTIONS["chat_references"] = [
                r for r in state._CONTRIBUTIONS["chat_references"] if r.get("plugin_id") != "refprobe"
            ]


def test_crypto_round_trips() -> None:
    key, nonce = b"k" * 32, b"n" * 12
    sealed = api._CryptoApi.aead_seal(key, nonce, b"hello", b"aad")
    assert api._CryptoApi.aead_open(key, nonce, sealed, b"aad") == b"hello"
