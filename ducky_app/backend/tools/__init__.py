"""Import always-on tool modules so @mcp.tool() registrations run.

Domain editor tools register via Store desktop plugins (uefn, verse, …).
"""

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
# Domain modules (actors, verse, niagara, …) register via uefn-plugin-* only.
# translation_tools / materials register via their plugins.
# Discord tools live entirely in uefn-plugin-discord (api.tool).
