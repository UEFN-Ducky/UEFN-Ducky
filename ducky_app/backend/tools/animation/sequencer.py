"""Moved: animation authoring tools now ship in the ``animation`` Store plugin.

Kept as an empty module so plugin zips at animation < 1.1.0 can still
``import backend.tools.animation.sequencer`` without failing plugin load. See
``animation_retarget.py`` in this package.
"""

from __future__ import annotations
