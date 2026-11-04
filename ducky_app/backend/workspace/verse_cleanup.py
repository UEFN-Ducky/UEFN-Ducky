"""After a revert deletes a created file: empty folders, the Verse class UEFN built, a rebuild."""

from __future__ import annotations

import os
import re

_VERSE_DEVICE_CLASS = re.compile(r"(?m)^class\s+(\w+_device)\b")


_KEEP_UNDER_CONTENT = frozenset({"verse", "python"})


def prune_empty_parents(abs_file: str, project_root: str) -> None:
    """Remove empty folders left after deleting a created file.

    Stops at Content/ and never removes Content/Verse or Content/Python.
    """
    from backend.workspace.paths import island_root

    parent = os.path.dirname(os.path.realpath(abs_file))
    island = os.path.realpath(island_root(project_root))
    content = os.path.realpath(os.path.join(island, "Content"))
    stop = content if os.path.isdir(content) else island
    while parent:
        here = os.path.realpath(parent)
        if here in (stop, island):
            break
        if os.path.dirname(here) == stop and os.path.basename(here).lower() in _KEEP_UNDER_CONTENT:
            break
        try:
            if os.listdir(parent):
                break
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


def _verse_device_names(source: str) -> list[str]:
    return _VERSE_DEVICE_CLASS.findall(source or "")


def revert_uefn_for_deleted_verse(rel_path: str, source: str, writer_root: str) -> None:
    """Drop the generated Verse class / placed device after the .verse file is gone.

    Offline is fine — the file (and empty folders) already match disk. UEFN is
    best-effort so Content Browser does not keep a ghost class.
    """
    names = _verse_device_names(source)
    if not names:
        return
    from backend.workspace.paths import island_root

    island = os.path.basename(island_root(writer_root).rstrip("\\/"))
    rel = rel_path.replace("\\", "/").lstrip("/")
    if rel.lower().startswith("content/"):
        rel = rel[8:]
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    try:
        from backend.workspace.plugin_revert import post_inverse_step
    except Exception:
        return
    for name in names:
        asset = f"/{island}/{folder}/{name}" if folder else f"/{island}/{name}"
        try:
            post_inverse_step(
                "ducky_revert_creation",
                {"kind": "asset", "id": asset, "guid": ""},
                program="uefn",
            )
        except Exception:
            pass
        for ident in (name, name.replace("_", "")):
            try:
                post_inverse_step(
                    "ducky_revert_creation",
                    {"kind": "actor", "id": ident, "guid": ""},
                    program="uefn",
                )
            except Exception:
                pass


def kick_verse_compile() -> None:
    """Rebuild so UEFN drops generated classes for deleted .verse files. Never blocks revert."""
    import threading

    def _run() -> None:
        try:
            from backend.tools.verse.verse_diagnostics import workspace_compile_verse

            workspace_compile_verse()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="changeset-verse-compile").start()
