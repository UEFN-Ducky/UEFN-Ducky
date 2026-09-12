"""Import always-on tool modules so @mcp.tool() registrations run.

Domain editor tools register via Store desktop plugins (uefn, verse, …).
"""

from backend.tools.core import changesets  # noqa: F401
from backend.tools.core import code_diagnostics  # noqa: F401
from backend.tools.core import hints  # noqa: F401
from backend.tools.core import system  # noqa: F401
from backend.tools.panel import ducky_panel  # noqa: F401
from backend.tools.panel import panel_ai_plugins  # noqa: F401
from backend.tools.panel import panel_i18n  # noqa: F401
from backend.tools.panel import panel_mcp  # noqa: F401
from backend.tools.panel import panel_profiles  # noqa: F401
from backend.tools.panel import panel_settings  # noqa: F401
from backend.tools.panel import panel_skills  # noqa: F401
from backend.tools.panel import panel_store  # noqa: F401
from backend.tools.panel import panel_ui  # noqa: F401
from backend.tools.panel import panel_verse_templates  # noqa: F401
# Host-disk Verse: register even when the verse Store plugin is off / MCP is down.
# Aliased: an unaliased `import verse` would bind the name `verse` in this package's
# namespace, shadowing the `backend.tools.verse` subpackage itself — after which
# `import backend.tools.verse.verse_editable as ve` resolves the attribute chain to
# verse.py and raises ImportError.
from backend.tools.verse import skill_tool as _verse_skill_tool  # noqa: F401
from backend.tools.verse import verse as _verse_tools  # noqa: F401
from backend.tools.verse import verse_diagnostics as _verse_diagnostics  # noqa: F401
# Domain editor tools (actors, niagara, …) still register via uefn-plugin-* only.
# translation_tools / materials register via their plugins.
# Discord tools live entirely in uefn-plugin-discord (api.tool).
