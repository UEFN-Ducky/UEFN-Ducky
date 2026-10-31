"""Pictures a tool hands an agent, in a shape every model API takes.

Anthropic refuses a picture over 5 MB (and any over 2000 px once a request holds more than
20 pictures); OpenAI refuses one that is not png / jpeg / webp / still gif. Either refusal is
a 400 that ends the turn, and the picture stays in the thread, so later turns fail too.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

MAX_SIDE = 1920
MAX_BYTES = 3_500_000  # about 4.7 MB once base64: under Anthropic's 5 MB
_AS_IS = (".png", ".jpg", ".jpeg", ".webp")


def model_picture(path: str | Path) -> Any:
    """A FastMCP Image of ``path``: the file itself when it already fits, otherwise scaled to
    MAX_SIDE as PNG (JPEG when the PNG is still over MAX_BYTES)."""
    from mcp.server.fastmcp import Image as ToolImage
    from PIL import Image

    source = Path(path)
    try:
        with Image.open(source) as img:
            if (source.suffix.lower() in _AS_IS and source.stat().st_size <= MAX_BYTES
                    and max(img.size) <= MAX_SIDE):
                return ToolImage(path=source)
            img.load()
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            picture = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Not a picture Ducky can open: {source.name}") from exc
    out = io.BytesIO()
    picture.save(out, "PNG")
    if out.tell() <= MAX_BYTES:
        return ToolImage(data=out.getvalue(), format="png")
    out = io.BytesIO()
    picture.convert("RGB").save(out, "JPEG", quality=85)
    return ToolImage(data=out.getvalue(), format="jpeg")
