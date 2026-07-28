"""Identical mcp.json uefn blocks must not rewrite the file (Cursor reconnect)."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path


def main() -> None:
    from frontend.merge import merge_uefn_into_config

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mcp.json"
        block = {"command": "C:/fake/UEFN-Ducky.exe", "args": ["bridge", "--port", "4200"]}
        path.write_text(
            json.dumps({"mcpServers": {"uefn": block, "other": {"command": "x"}}}, indent=2),
            encoding="utf-8",
        )
        mtime1 = path.stat().st_mtime_ns
        time.sleep(0.05)
        merge_uefn_into_config(path, dict(block), dry_run=False)
        mtime2 = path.stat().st_mtime_ns
        assert mtime1 == mtime2, "noop merge must not touch mcp.json mtime"
        changed = dict(block)
        changed["args"] = ["bridge", "--port", "4201"]
        merge_uefn_into_config(path, changed, dry_run=False)
        mtime3 = path.stat().st_mtime_ns
        assert mtime3 != mtime2, "changed block must write"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["other"]["command"] == "x"
        assert data["mcpServers"]["uefn"]["args"][-1] == "4201"
    print("ok merge skips identical uefn block")


if __name__ == "__main__":
    main()
