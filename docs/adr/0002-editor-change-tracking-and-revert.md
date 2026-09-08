# ADR 0002: Editor change tracking, blocked-attempt recording, and revert

Status: Accepted (2026-09-08)

Extends [ADR 0001](0001-write-through-with-lanes-and-changesets.md), which covers
project **file** writes. This record covers everything a ducky changes that is
not a file.

## Context

ADR 0001 gave every project file write one pipeline, one identity, one ledger
and a revert. It covered nothing a ducky does inside the editor. Spawning an
actor, moving it, wiring an `@editable`, setting a device property, creating a
material or running `execute_python` was unattributed, unledgered and
unrevertable.

The record was also success-only. A lane denial raised `WriteDenied` and emitted
a chat event; a delete refusal raised inside the listener; a failed tool call was
a red row in the transcript. None of them reached the ledger, so the history read
as though the attempt had never happened.

A ledger that omits editor work and silently drops refusals is not a ledger.

Three properties of the system made this tractable:

- **The host is the sole gateway to the editor.** Every Ducky editor mutation
  passes through `backend/bridge/client.py` (`send_command`,
  `post_command_to_listener`); Epic's `unreal__*` tools share
  `backend/mcp_plugins/client_pool.py` (`call_tool`). Three hook points, not
  dozens of call sites.
- **The listener has one dispatch chokepoint.** `uefn_listener/listener/tick.py`
  sees every command regardless of which handler or Store plugin registered it,
  and the listener hot-reloads from AppData, so changes ship without a UEFN
  restart.
- **The response envelope has room.** Both host readers touch only `success`,
  `error`, `traceback` and `result`, so an additive sibling key is invisible in
  both mixed-version directions.

## Decision

1. **Hook the tick, not the handlers.** `ducky_capture.before` / `.after` wrap
   the single `dispatch` call. Both swallow every exception: a capture bug must
   never turn a working edit into an error, and must never raise inside the
   editor tick. An unregistered command costs one dict lookup.

2. **Report captures on an additive `_ducky` envelope key**, versioned `v: 1`.
   A listener that does not send it degrades to a recorded change with no
   inverse, not to no record at all.

3. **Two registries, two homes, one invariant.** The host owns *"does this
   mutate?"* (`backend/workspace/editor_ops.py`, one row per listener command)
   because it is the sole gateway. The listener owns *"how do I read the
   before-state and build the inverse?"* (`ducky_capture.py`) because that needs
   `unreal` and must run in-tick. A parity test asserts every capture command is
   in the host table as mutating, and a completeness test parses the listener
   tree so no command can be added unclassified.

   Unknown commands classify as **opaque**, never as harmless. Over-recording
   costs an empty row; under-recording loses a change.

4. **A journal slot is a target and a facet, not a call.**
   `uefn://actor/<guid>/transform`, `uefn://device/<guid>/settings/<key>`, and so
   on. Three nudges to one actor share a slot, so they collapse into one row and
   one restore to the state before the run touched it — which is what the
   existing journal semantics already do for repeated writes to one file.

   Opaque commands are the exception: they have no target to name, and running a
   script twice is two events rather than one thing edited twice. They get
   `uefn://opaque/<command>/<run_id>-<n>`, unique per call.

5. **Actor GUID is the durable identity.** Labels are neither unique nor stable
   (`set_actor_label` changes the index key) and paths change on rename. GUID is
   requestable but absent from `DEFAULT_ACTOR_FIELDS`, because `serialize_actor`
   returns every getter when no fields are asked for and `get_all_actors` relies
   on that — adding it by default would put a reflection call into every full
   level scan.

6. **Reverts are compensating forward mutations, never rollbacks.** No editor
   undo API is used anywhere in the tree, and the save coalescer flushes roughly
   0.4 s after the last edit, so by the time anyone clicks Revert the change is
   already on disk. A revert posts the inverse the listener recorded, as its own
   attributed revert run. The UI must never promise otherwise.

7. **Revertability is a claim, and it is only made when it is true.**

   | Tier | Meaning |
   |---|---|
   | `auto` | The listener recorded either an inverse that can be posted back, or exactly what was created |
   | `manual` | Recorded, but the prior state is genuinely gone or was never knowable — carries a reason a human can act on |
   | `none` | Nothing changed (the command failed or was refused), so there is nothing to undo |

   A spec's tier is a **ceiling**. A call that produced neither an inverse nor an
   identified creation is downgraded to `manual` even when its spec says `auto`.
   A lossy read (`fill_data_table_from_*` read back through `get_data_table_rows`,
   which caps columns and truncates cells) is labelled lossy and never presented
   as a backup.

8. **Undoing a creation is a delete, so it gets exactly one narrow path with the
   authority deliberately split.** `delete_actors`, `delete_asset` and the
   `execute_python` static block are unchanged and remain unreachable for agents.
   A single internal listener command, `ducky_revert_creation`, removes one
   recorded creation under a five-point contract: one id per call with no bulk
   form; `actor` or `asset` only; assets inside this project's content only;
   actors matched by recorded GUID first, refusing when the live GUID differs;
   and assets refused while anything still references them, with the referencer
   list in the refusal.

   **The journal is the provenance authority — it only ever asks for something it
   recorded this agent creating, in a run the user chose to revert. The listener
   is the mechanical authority — it re-checks independently and refuses anything
   it cannot prove is safe.** Neither side alone is trusted.

   It is kept away from agents four ways: never auto-exposed as a passthrough
   tool, refused by name through `listener_command`, blocked in plan mode, and
   only ever called by a user-initiated revert. The prompt rules say the same in
   words, so a ducky does not reason its way toward asking for it.

9. **Blocked and failed attempts are recorded.** Every journal entry carries an
   `outcome` of `ok`, `blocked` or `failed`, defaulting to `ok`. Non-`ok` entries
   carry a reason and the attempted target, and are excluded from revert
   (nothing to undo), from `index.json` (nothing landed), from conflict
   detection, from `export_run.files` and from every file count. They appear only
   in the timeline, visually distinct. Only mutating commands are recorded this
   way: a failed read is not a change.

10. **Opaque commands are bracketed, not modelled.** `execute_python` and
    `exec_console_command` take a level snapshot either side of the call, in the
    listener, in one tick — host-orchestrated bracketing would be three round
    trips with the level free to change between them. Only the *difference* is
    journaled, never the two snapshots, alongside the code verbatim. Actors that
    appeared are recorded as creations, which is the highest-value case: a script
    that spawns forty props becomes one Revert that removes exactly those forty.

11. **Lanes do not apply to editor targets.** Entries record `in_lane: null` and
    show no lane badge, for three reasons: lanes are a public contract over
    project-relative *file paths*; editor targets live in three unrelated
    namespaces (actor GUIDs, Outliner folders, `/Game/...` package paths); and
    the `/Game` to `Content` mapping is lossy, since Verse device classes live in
    mangled packages and level actors have no file of their own. A lane
    **denial** is still recorded — it is the attempt that is logged, not a lane
    check on an editor target.

12. **The ledger has a home of its own.** A singleton editor tab, project-wide,
    grouped by run. The per-chat Context panel stays as a compact summary and
    links into it.

## Alternatives considered

- **Per-handler capture in each of ~120 listener handlers.** Complete but
  unmaintainable, and every new handler would silently opt out. Rejected for the
  single tick hook.
- **A new top-level key inside `result`.** `result` is the payload agents parse;
  adding to it changes what every tool returns. Rejected for the envelope.
- **Putting editor changes through `ProjectWriter`.** The pipeline's observers
  (file history, follow-code editor sync) would be handed `uefn://` pseudo-paths
  they would try to open. Rejected: the journal is called directly.
- **A separate blob key for `execute_python`'s code.** The journal's blob GC
  walks only `before_blob` and `after_blob`, so a third key would be collected
  out from under a live run. Rejected: the code goes in the after blob.
- **Duplicating assets as backups before a destructive change.** Real undo, but
  it doubles asset storage on every material edit for a case that is already
  honestly reported as manual. Rejected.
- **A schema version bump.** Every addition here is optional and additive, so
  `schema_version` stays at 1 and old documents keep validating.
- **Widening the existing delete refusals for revert.** Would make the refusal
  conditional, and a conditional refusal is one prompt-injection away from not
  being one. Rejected for the separate, unexposed, journal-only command.

## Consequences

- Every change a ducky makes — file or editor, applied or refused — is
  attributed, ordered, visible and, where it honestly can be, revertable.
- Recording is best-effort by construction: capture failures, snapshot failures
  and observer failures are all swallowed. A missing row is possible; a broken
  command because of bookkeeping is not.
- A level snapshot is refused above ~6000 actors, so on very large levels an
  opaque command records its code but not its effect, and says so.
- The Epic `unreal__*` classification depends on tool annotations being
  preserved through the plugin client pool. Without them it falls back to a
  name-prefix rule, which is why annotations are restored as their own change.
- `ducky_revert_creation` is the one place in the tree where an agent-authored
  change leads to a deletion. It is the most security-sensitive code added here
  and is tested along every refusal path.
