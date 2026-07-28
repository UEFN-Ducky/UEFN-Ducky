"""Bulk/batch listener commands were removed — single-op tools only."""

BULK_REMOVED_MSG = (
    "batch/bulk commands removed — use single-op MCP tools "
    "(wire_verse_device_ref, spawn_actor, set_verse_editable, set_device_settings, …)"
)


def bulk_removed_error() -> dict:
    return {"ok": False, "error": "bulk_removed", "message": BULK_REMOVED_MSG}
