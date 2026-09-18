"""Desktop Automations: graphs, runner, plugin nodes, panel-process scheduler."""

from backend.automations.catalog import list_nodes as list_automation_nodes
from backend.automations.runner import emit_automation, run_automation, run_pipeline
from backend.automations.store import (
    delete_automation,
    get_automation,
    list_automations,
    save_automation,
)

__all__ = [
    "delete_automation",
    "emit_automation",
    "get_automation",
    "list_automation_nodes",
    "list_automations",
    "run_automation",
    "run_pipeline",
    "save_automation",
]
