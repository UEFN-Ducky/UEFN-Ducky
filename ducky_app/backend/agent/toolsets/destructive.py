"""Tools that require human approval before the agent runs them."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def allow_destructive_execution(
    pending: Sequence[Any],
    approval_callback: Callable[[Sequence[Any]], bool] | None,
) -> bool:
    """Never auto-approve. No callback → refuse. Callback False → refuse."""
    if not pending:
        return True
    if approval_callback is None:
        return False
    return bool(approval_callback(pending))


DESTRUCTIVE_TOOLS = frozenset(
    {
        "shutdown",
        # Replace ALL rows of a data table in one call.
        "fill_data_table_from_json",
        "fill_data_table_from_csv",
        # Removes an entity and everything under it (children + components).
        "destroy_entity",
        # Deletes a socket from the Skeleton asset — shared by EVERY mesh on
        # that skeleton, including runtime-spawned NPC definitions.
        "remove_skeleton_socket",
        # One-way actor -> Scene Graph entity migration.
        "convert_actors_to_entities",
    }
)
