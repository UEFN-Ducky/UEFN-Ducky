"""Bridge tests list their own fake tools (``ping``...): off Windows, the no-UEFN-route
filter (plugin_gate) would hide them and ask the account for linked PCs. The route is
pinned to ``local`` for the session (env, so daemons the tests start inherit it and a
test's ``monkeypatch.undo()`` keeps it); route tests clear it with ``monkeypatch.delenv``."""

from __future__ import annotations

import os

from backend.bridge.editor_presence import ROUTE_ENV

os.environ.setdefault(ROUTE_ENV, "local")
