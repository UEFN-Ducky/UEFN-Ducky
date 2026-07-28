# Import side effect: registers all commands on listener.dispatch.
from listener.handlers import actors  # noqa: F401
from listener.handlers import assets  # noqa: F401
from listener.handlers import device_editor  # noqa: F401
from listener.handlers import project  # noqa: F401
from listener.handlers import system  # noqa: F401
from listener.handlers import verse_editable  # noqa: F401
from listener.handlers import asset_preview  # noqa: F401
import listener.registry  # noqa: F401 — registers domain commands (materials, animation, …)
