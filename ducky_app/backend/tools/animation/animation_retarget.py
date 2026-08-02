"""Moved: IK retarget tools now ship in the ``animation`` Store plugin.

Kept as an empty module because plugin zips at animation < 1.1.0 do
``import backend.tools.animation.animation_retarget`` in ``register()`` — an
ImportError there would fail the whole plugin load. Those builds simply register
no animation tools until the Store update lands (which brings both the MCP tools
and their listener handlers).
"""

from __future__ import annotations
