"""Refuse to freeze a stale MCP dependency set from a reused build environment."""
from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

CHECKED = ("mcp", "anyio", "httpx")


def validate(requirements: Path) -> dict[str, str]:
    text = requirements.read_text(encoding="utf-8")
    versions = {}
    for name in CHECKED:
        match = re.search(rf"^{name}(?:\[[^\]]+\])?==([^\s;#]+)\s*$", text, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"Release dependency {name} needs an exact tested version in {requirements.name}")
        expected = match.group(1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"Refusing to package {name} {actual}; requirements specify {expected}. Install requirements first.")
        versions[name] = actual
    return versions
