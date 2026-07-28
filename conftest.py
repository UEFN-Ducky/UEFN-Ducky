"""Make `py -m pytest` work from the repo root.

Tests import ``frontend`` / ``backend`` directly (the same layout the frozen exe
and ``cd ducky_app && py -m frontend`` use), so the ``ducky_app`` directory must
be on ``sys.path``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ducky_app"))
