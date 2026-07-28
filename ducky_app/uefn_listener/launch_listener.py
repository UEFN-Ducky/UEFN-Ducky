"""UEFN: Tools → Execute Python Script → open this file (``ducky_app/uefn_listener/launch_listener.py``)."""

from __future__ import annotations

import os
import sys

_ed = os.path.dirname(os.path.abspath(__file__))
if _ed not in sys.path:
    sys.path.insert(0, _ed)

from listener.bootstrap import run

run()
