# The write pipeline

Files only. Everything else a ducky changes — actors, devices, assets, Verse
wiring, scripts — is covered by
[editor change tracking](editor-change-tracking.md), which lands in the same
per-run ledger.

Every mutation of a file in the user's UEFN project goes through
`backend.workspace.writer.ProjectWriter`. Nothing else may open a project file
for writing; `backend/workspace/test_no_direct_writes.py` enforces that.

```
tool / panel / external-agent observer
        │  ProjectWriter.write_text / create / path_op
        ▼
 1 resolve   backend.bridge.resolve_workspace_path + paths.require_* guards
 2 identity  RunContext.current() → from_env() → user
 3 policy    WritePolicy chain (LanePolicy in off | shadow | enforce)
 4 lock      per-path threading.Lock; optional expected_hash (compare-and-swap)
 5 read      before-content + hash
 6 write     tmp file + os.replace (atomic)
 7 journal   ChangeJournal.record → run document, blobs, last-writer index
 8 notify    WriteObserver fan-out: file history, editor sync, UI events
```

## Protocols

| Protocol | Who implements it | Registered where |
|---|---|---|
| `WritePolicy` | `LanePolicy` (backend) | `runtime.configure(policies=...)` |
| `WriteObserver` | file history, editor sync (frontend) | `runtime.configure(observers=...)` |
| `ChangeJournal` | `FileChangeJournal` (backend, M2) | `runtime.configure(journal=...)` |
| event sink | panel push (frontend) | `events.register_sink` |
| `LaneProvider` | roster provider (frontend), plugins | `lanes.register_lane_provider` |

The composition root is `frontend.ui_web.workspace_bootstrap` (panel process).
The MCP bridge process used by external coding agents gets the same defaults
from `backend.workspace.runtime.get_writer()`; identity comes from the
`DUCKY_*` environment there.

## Adding a write path

Call the pipeline. For text: `get_writer().write_text(rel, content, tool="my_tool")`.
For rename/move/delete the filesystem step stays with the caller and is passed
in as `perform`; the pipeline owns policy, locking, journal, and events:

```python
get_writer().path_op("move", src_rel, dest_rel, tool="move_project_entry", perform=do_move)
```

## Failure semantics

- A policy denial raises `WriteDenied` (a `ValueError`) before any disk change.
- A stale `expected_hash` raises `StaleWrite` before any disk change.
- A journal or observer failure never fails a write that already landed; it is
  logged at WARNING and reported in `WriteResult.warning`.

## Known limitation

External coding agents (Claude Code, Codex, Cursor) use their own `Write`/`Edit`
tools inside their own process. Ducky observes those edits after the fact and
journals them with attribution and `in_lane`, but cannot block them.
