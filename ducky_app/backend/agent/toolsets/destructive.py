"""Tools that require human approval before the agent runs them."""

from __future__ import annotations

DESTRUCTIVE_TOOLS = frozenset(
    {
        "shutdown",
        "delete_actors",
        "delete_asset",
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
