"""Normalize MCP / FastMCP call_tool return values to plain text."""

from __future__ import annotations

import json
from typing import Any


def mcp_content_to_text(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    # FastMCP call_tool returns (content_blocks, meta_dict) — use blocks only.
    if isinstance(blocks, tuple) and len(blocks) >= 1:
        head = blocks[0]
        if isinstance(head, (list, tuple)):
            return mcp_content_to_text(head)
        if isinstance(head, str):
            return head
    if isinstance(blocks, dict):
        if "result" in blocks:
            return mcp_content_to_text(blocks["result"])
        return json.dumps(blocks, ensure_ascii=False)
    parts: list[str] = []
    if isinstance(blocks, (list, tuple)):
        for block in blocks:
            # Skip image/audio content — vision is attached separately; never
            # stringify base64 blobs into the tool-result text envelope.
            btype = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if btype in ("image", "audio", "resource"):
                continue
            if hasattr(block, "text") and getattr(block, "type", "text") in (None, "text"):
                parts.append(str(block.text))
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, (list, tuple)):
                nested = mcp_content_to_text(block)
                if nested:
                    parts.append(nested)
            elif isinstance(block, dict):
                continue
            elif hasattr(block, "text"):
                parts.append(str(block.text))
            else:
                # Avoid dumping ImageContent/AudioContent reprs into the LLM.
                continue
    return "\n".join(parts) if parts else ("" if isinstance(blocks, (list, tuple)) else str(blocks))
