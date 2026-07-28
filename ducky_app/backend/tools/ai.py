"""AI-powered, host-side MCP tools.

These run in the host process (not the editor), so they need no listener
redeploy. They call the Anthropic API to turn a natural-language request into
concrete listener/registry commands and then execute them through the existing
bridge. Everything is gated behind ``ANTHROPIC_API_KEY``: if the key (or the
``anthropic`` package) is missing, the tools return a clear message and the rest
of the server keeps working.
"""

from __future__ import annotations

import os
import re
from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool
from backend.uefn_editor_api_hints import hints_for_topic

# Latest Claude models (see the claude-api skill / Anthropic docs).
_DEFAULT_MODEL = os.environ.get("UEFN_DUCKY_AI_MODEL", "claude-opus-4-8")
_FALLBACK_MODEL = "claude-sonnet-4-6"
_NOT_CONFIGURED = (
    "AI tools are not configured. Set the ANTHROPIC_API_KEY environment variable "
    "and `pip install anthropic` in the host environment to enable ai_generate_python."
)


def _client():
    """Return (client, None) or (None, message) if AI is unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, _NOT_CONFIGURED
    try:
        import anthropic  # lazy: optional dependency
    except ImportError:
        return None, _NOT_CONFIGURED
    return anthropic.Anthropic(), None


def _complete(system: str, user: str, model: str, max_tokens: int = 4096) -> str:
    client, err = _client()
    if client is None:
        raise RuntimeError(err)
    chosen = model or _DEFAULT_MODEL
    try:
        msg = client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception:
        if chosen != _FALLBACK_MODEL:
            msg = client.messages.create(
                model=_FALLBACK_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        else:
            raise
    return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


def _extract_code_block(text: str, lang: str = "") -> str:
    """Pull the first fenced code block out of model output, or return as-is."""
    pattern = r"```(?:" + (lang or r"[a-zA-Z]*") + r")?\s*\n(.*?)```"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _command_catalog() -> dict:
    """Compact catalog of what the editor can do right now, for grounding the model."""
    catalog: dict[str, Any] = {"commands": []}
    try:
        catalog["commands"] = send_command("ping").get("commands", [])
    except Exception as e:
        catalog["commands_error"] = str(e)
    return catalog


@plugin_mcp_tool("uefn")
def ai_generate_python(goal: str, run: bool = False, topic: str = "all", model: str = "", pretty: bool = False) -> str:
    """Generate editor-side Python for a goal (and optionally execute it).

    Injects the uefn_editor_python_hints for the given topic so the generated code
    avoids common reflection mistakes. Requires ANTHROPIC_API_KEY.

    Args:
        goal: What the script should accomplish.
        run: If true, execute the generated code via execute_python and include output.
        topic: Hints topic (all/general/materials/actors/save/...).
        model: Override model id (default claude-opus-4-8).
    """
    client, err = _client()
    if client is None:
        return err

    hints = hints_for_topic(topic)
    system = (
        "You write Python to run inside the UEFN editor via execute_python. The globals "
        "unreal, actor_sub, asset_sub, level_sub, tk, get_tk_root are available; assign the "
        "final value to a variable named `result`. Output ONLY a Python code block, no prose. "
        "Follow these API notes:\n" + hints
    )
    raw = _complete(system, goal, model)
    code = _extract_code_block(raw, "python")

    if not run:
        return tool_json({"code": code, "executed": False}, pretty=pretty)

    result = send_command("execute_python", {"code": code})
    return tool_json({"code": code, "result": result, "executed": True}, pretty=pretty)
