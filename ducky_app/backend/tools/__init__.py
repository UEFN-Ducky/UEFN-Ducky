"""Import tool modules so @mcp.tool() registrations run.

Domain editor tools register via Store desktop plugins (uefn, verse, …).
"""

from backend.tools import code_diagnostics  # noqa: F401
from backend.tools import ducky_panel  # noqa: F401
from backend.tools import hints  # noqa: F401
from backend.tools import panel_ai_plugins  # noqa: F401
from backend.tools import panel_i18n  # noqa: F401
from backend.tools import panel_mcp  # noqa: F401
from backend.tools import panel_profiles  # noqa: F401
from backend.tools import panel_settings  # noqa: F401
from backend.tools import panel_skills  # noqa: F401
from backend.tools import panel_store  # noqa: F401
from backend.tools import panel_ui  # noqa: F401
from backend.tools import panel_verse_templates  # noqa: F401
from backend.tools import system  # noqa: F401
# Domain modules (actors, verse, niagara, …) register via uefn-plugin-* only.
# discord_tools / translation_tools / materials register via their plugins.
