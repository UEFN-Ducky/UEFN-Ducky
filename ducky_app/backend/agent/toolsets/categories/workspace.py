"""Host-disk project files — Cursor-style read/write under the UEFN project root."""

from __future__ import annotations

CORE_TOOLS = frozenset(
    {
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_write_file",
        "workspace_list_verse_errors",
    }
)

EXTENDED_TOOLS = frozenset(
    {
        "workspace_compile_verse",
        "code_list_errors",
        "code_detect_project",
        "workspace_open_verse_file",
        "workspace_push_verse_changes",
        "code_open_file",
        "workspace_edit_file",
        "workspace_search",
        "workspace_find",
        "workspace_read_files",
        "workspace_file_outline",
        "workspace_tree",
        "workspace_multi_edit",
        "workspace_replace_lines",
        "workspace_move_file",
        "workspace_delete_file",
        "workspace_git",
    }
)

TOOLS = CORE_TOOLS | EXTENDED_TOOLS

PLAN_TOOLS = frozenset(
    {
        "workspace_list_dir",
        "workspace_read_file",
        "workspace_list_verse_errors",
    }
)
