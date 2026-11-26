"""Refuse to freeze a stale MCP dependency set from a reused build environment."""
from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path

CHECKED = ("mcp", "anyio", "httpx", "pywebview")
# The Mac window runs on PyObjC; its packages must all be the tested release.
CHECKED_MAC = ("pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit")


def validate(requirements: Path) -> dict[str, str]:
    text = requirements.read_text(encoding="utf-8")
    names = CHECKED + (CHECKED_MAC if sys.platform == "darwin" else ())
    versions = {}
    for name in names:
        match = re.search(rf"^{re.escape(name)}(?:\[[^\]]+\])?==([^\s;#]+)\s*(?:;[^#\n]*)?$", text, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"Release dependency {name} needs an exact tested version in {requirements.name}")
        expected = match.group(1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"Refusing to package {name} {actual}; requirements specify {expected}. Install requirements first.")
        versions[name] = actual
    return versions
