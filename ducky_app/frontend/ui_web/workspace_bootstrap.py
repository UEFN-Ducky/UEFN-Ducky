"""Composition root: wires frontend adapters into the backend write pipeline.

Called once per process: the panel (``webview_app``) and the MCP bridge
(``launcher.run_bridge``). Cheap by design so it can sit on the bridge's
sub-second start path: heavy imports happen lazily inside the adapters.

Adapters here are the only place the pipeline learns about frontend concerns
(file history, follow-code editor batches, panel events, conversations).
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable

from backend.workspace import editor_record, events
from backend.workspace.identity import SOURCE_USER, RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.lanes import LanePolicy, set_mode_source
from backend.workspace.runtime import configure
from backend.workspace.writer import WriteRecord, WriteResult

log = logging.getLogger(__name__)

_installed = False
_lock = threading.Lock()

_TEXT_OPS = frozenset({"write", "create"})


class FileHistoryObserver:
    """Every applied write leaves a restore point and an attributed version."""

    def on_write(self, record: WriteRecord, result: WriteResult) -> None:
        from frontend.ui_web.verse_editor import file_history

        if record.op in _TEXT_OPS:
            file_history.record_write(
                record.path,
                record.before if record.existed_before else None,
                record.after,
                project_root=record.project_root,
                writer=record.writer,
            )
        elif record.op == "delete" and record.before:
            # The removed text stays recoverable from history even after the trash is purged.
            file_history.snapshot_before_write(record.path, record.before, record.project_root)


class EditorSyncObserver:
    """Follow-code editor batches for agent writes made from the MCP bridge process.

    ``emit_for_bridge_tool`` is a no-op inside the panel process (the agent loop
    emits there from ``tool_done``), so registering this in both processes is safe.
    """

    def on_write(self, record: WriteRecord, result: WriteResult) -> None:
        if record.op not in _TEXT_OPS or record.writer.get("source") == SOURCE_USER:
            return
        from frontend.ui_web.verse_editor.agent_sync import emit_for_bridge_tool, is_write_tool

        tool = record.tool if is_write_tool(record.tool) else "workspace_write_file"
        emit_for_bridge_tool(
            tool,
            {"relative_path": record.path, "content": record.after},
            {
                "data": {
                    "relative_path": record.path,
                    "before_content": record.before,
                    "lines_added": record.lines_added,
                    "lines_removed": record.lines_removed,
                }
            },
            conv_id=str(record.writer.get("conv_id") or ""),
        )


def _changesets_storage(project_root: str) -> Path:
    """AppData/changesets/<project slug>, beside file_history and the chat store.

    The writer / MCP tools hand us the first Verse workspace folder
    (``…/<Project>/Content``); the Changes tab asks by the panel's project root
    (``…/<Project>``). Climb to the folder holding ``*.uefnproject`` so both
    land in one ledger instead of a ``Content_<hash>`` orphan.
    """
    from frontend.settings import default_app_data_dir
    from frontend.ui_web.project_chats import project_slug

    p = Path(project_root).resolve()
    for cand in (p, *p.parents[:3]):
        if any(cand.glob("*.uefnproject")):
            p = cand
            break
    base = default_app_data_dir() / "changesets"
    storage = base / project_slug(str(p))
    _fold_orphan_ledger(base / project_slug(str(p / "Content")), storage)
    return storage


_folded: set[str] = set()


def _fold_orphan_ledger(orphan: Path, storage: Path) -> None:
    """Merge a pre-fix ``Content_<hash>`` ledger into the project ledger, once per process."""
    key = str(orphan)
    if key in _folded:
        return
    _folded.add(key)
    if orphan == storage or not orphan.is_dir():
        return
    try:
        for sub in ("runs", "blobs"):
            (storage / sub).mkdir(parents=True, exist_ok=True)
            for f in (orphan / sub).glob("*"):
                target = storage / sub / f.name
                if not target.exists() or (sub == "runs" and f.stat().st_mtime > target.stat().st_mtime):
                    shutil.move(str(f), str(target))
        for name in ("catalog.json", "index.json"):
            src = orphan / name
            if not src.is_file():
                continue
            dst = storage / name
            merged = json.loads(dst.read_text(encoding="utf-8")) if dst.is_file() else {}
            for k, v in json.loads(src.read_text(encoding="utf-8")).items():
                cur = merged.get(k)
                if cur is None or float((v or {}).get("ts") or 0) > float((cur or {}).get("ts") or 0):
                    merged[k] = v
            dst.write_text(json.dumps(merged, indent=1), encoding="utf-8")
        shutil.rmtree(orphan, ignore_errors=True)
    except Exception:
        pass  # best-effort heal; the canonical ledger still works without it


def _journal_enabled() -> bool:
    from frontend.settings import PanelSettings

    return bool(getattr(PanelSettings.load(), "changeset_journal_enabled", True))


def build_journal() -> FileChangeJournal:
    return FileChangeJournal(_changesets_storage, enabled=_journal_enabled)


def _panel_event_sink(event: dict[str, Any]) -> None:
    from frontend.ui_web.verse_editor.panel_events import push_agent_event

    push_agent_event(event)


def install() -> None:
    """Register the frontend adapters on the process writer. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        from frontend.ui_web.lanes import install as install_lane_provider, lane_mode

        install_lane_provider()
        set_mode_source(lane_mode)
        configure(
            observers=[FileHistoryObserver(), EditorSyncObserver()],
            policies=[LanePolicy(mode=lane_mode)],
            journal=build_journal(),
        )
        # Editor changes (actors, assets, devices, Verse wiring) land in the same
        # per-run ledger as file writes, so one Revert covers a whole turn.
        editor_record.add_observer(editor_record.JournalEditorObserver())
        events.register_sink(_panel_event_sink)
        _installed = True


def reset_for_tests() -> None:
    global _installed
    with _lock:
        _installed = False


def build_run_context(
    conv: Any,
    *,
    run_id: str,
    model: str = "",
    coding_agent: str = "",
) -> RunContext:
    """Identity for one run of *conv*, including its group and leader when it is a member."""
    group_id = ""
    leader_conv_id = ""
    parent_id = str(getattr(conv, "parent_conv_id", "") or "").strip()
    if parent_id:
        try:
            from frontend.ui_web.group_orchestrator import is_group_conversation
            from frontend.ui_web.project_chats import load_conversation

            parent = load_conversation(parent_id)
            if parent is not None and is_group_conversation(parent):
                group_id = parent.id
                leader_conv_id = str(getattr(parent, "leader_conv_id", "") or "").strip()
        except Exception:  # noqa: BLE001 - identity must never fail a run
            log.debug("group lookup failed for %s", conv.id, exc_info=True)
    return RunContext(
        run_id=run_id,
        conv_id=conv.id,
        profile_id=str(getattr(conv, "profile_id", "") or "").strip(),
        # Tab title is what the user sees (Animation Engineer). ducky_name is the
        # library profile (Verse Coder) and must not leak into the Changes ledger.
        ducky_name=str(getattr(conv, "title", "") or getattr(conv, "ducky_name", "") or "").strip(),
        model=(model or str(getattr(conv, "model", "") or "")).strip(),
        provider=str(getattr(conv, "provider", "") or ""),
        coding_agent=(coding_agent or str(getattr(conv, "coding_agent", "") or "") or "ducky").strip(),
        group_id=group_id,
        leader_conv_id=leader_conv_id,
        is_leader=bool(leader_conv_id and leader_conv_id == conv.id),
    )


def record_external_edits(blocks: Iterable[dict[str, Any]] | None, ctx: RunContext) -> list[str]:
    """Attribute an external coding agent's native Write/Edit calls after its run.

    Claude Code, Codex and Cursor edit files in their own process, outside the
    pipeline. Their transcript blocks name the files; the current disk content is
    recorded as an attributed version (deduplicated by hash, so files already
    written through Ducky's own MCP tools are not double-counted).
    """
    from frontend.ui_web.verse_editor import agent_sync, file_history, io

    recorded: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict) or block.get("type") != "tool_call":
            continue
        status = str(block.get("status") or "success").lower()
        if status not in ("success", "ok", "done"):
            continue
        name = agent_sync.normalize_workspace_tool_name(str(block.get("name") or ""))
        if not agent_sync.is_write_tool(name):
            continue
        args = block.get("arguments")
        path = agent_sync.project_path_from_args(args if isinstance(args, dict) else {})
        if not path:
            continue
        try:
            content = io.read_file(path)["content"]
        except Exception:  # noqa: BLE001 - deleted, binary, or outside the project
            continue
        try:
            file_history.record_write(path, None, content, writer=ctx.as_writer(tool=name))
        except Exception:  # noqa: BLE001 - history must never fail the run
            log.warning("external edit attribution failed for %s", path, exc_info=True)
            continue
        recorded.append(path)
    return recorded
