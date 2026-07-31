"""Verse domain MCP tools (Store plugin: uefn-plugin-verse).

Importing this package loads ``verse`` so legacy ``import backend.tools.verse``
still registers the core digest tools.
"""
from . import verse as _verse  # noqa: F401
