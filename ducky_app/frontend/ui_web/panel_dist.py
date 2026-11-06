"""The built panel page is whole: every asset index.html names is on disk.

Moved out of panel_httpd.py, which had grown past its size cap; panel_httpd imports it
under the same name (tests patch panel_httpd.verify_panel_dist).
"""

from __future__ import annotations

import re
from pathlib import Path


def verify_panel_dist(dist_root: Path) -> None:
    """Raise if ``index.html`` references bundled assets that are missing on disk."""
    index = dist_root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"Panel dist missing: {index}")
    text = index.read_text(encoding="utf-8")
    for match in re.finditer(r"""(?:src|href)=["'](\./[^"']+)["']""", text):
        rel = match.group(1).removeprefix("./")
        asset = dist_root / rel
        if not asset.is_file():
            raise FileNotFoundError(
                f"Panel build incomplete: index.html references {rel} but file is missing at {asset}. "
                "Run: cd ducky_app/frontend/ui_web/web && npm run build"
            )
