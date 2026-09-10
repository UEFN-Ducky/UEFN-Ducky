# Editor change tracking

The companion to [the write pipeline](write-pipeline.md). That one covers files.
This one covers everything else a ducky changes: actors, devices, assets, Verse
wiring, and arbitrary scripts. Both end in the same per-run ledger, so a run's
history reads as one sequence rather than two.

The reasoning behind these choices is in
[ADR 0002](../adr/0002-editor-change-tracking-and-revert.md).

```
agent tool call
      │
      ├─ Ducky listener tool ──────► bridge.send_command ─────────┐
      ├─ listener_command / passthrough ────────────────────────  │
      ├─ Store plugin api.listener ─────────────────────────────  │  host: classify → record
      └─ Epic unreal__* ──► mcp_plugins.client_pool.call_tool ────┘  (ok | blocked | failed)
                                    │
                          HTTP ─────▼───── UEFN listener
                                 tick.dispatch
                       ducky_capture.before(cmd, params)      ← reads what is about to change
                              dispatch(cmd, params)
                       ducky_capture.after(...) ─────────────► response["_ducky"] sidecar
                                    │
      backend.workspace.editor_record.record ◄────────────────┘
                                    │
              journal entry  op:"editor"  (same run document as file writes)
                                    │
        ┌───────────────────────────┴────────────────────────┐
   Changes tab (project-wide timeline)      Context panel (this chat, compact)
```

## The two registries

| | Host `backend/workspace/editor_ops.py` | Listener `uefn_listener/listener/ducky_capture.py` |
|---|---|---|
| Answers | Does this command mutate? What kind of thing, what facet, what is the most it could be revertable? | What did this look like before, and what would put it back? |
| Needs | Nothing | `unreal`, and to run inside the tick |
| Unknown command | `opaque` — recorded, never assumed harmless (unless named `get_*` / `list_*` / `*_capabilities`: a plugin read) | No spec, no sidecar; the host still records it |

`ducky_capture` keeps every `unreal` and `listener.*` import inside a function,
so the host test suite can load it against fakes and check the two tables agree.
That property is itself asserted.

`_HEAVY_COMMANDS` in `tick.py` is a **throttle** list. It is not, and has never
been, a list of mutating commands: it contains reads such as `search_assets` and
`get_all_actors`. Do not seed one from the other.

## Slots

A journal `path` for an editor change is a **target and a facet**, not a call:

```
uefn://actor/<guid|path>/transform | /label | /folder | /tags | /attach | /props | /exists
uefn://device/<guid|path>/settings
uefn://verse/<guid|path>/editable
uefn://asset/<AssetPath>/exists | /props | /collision | /sockets | /chains
uefn://material/<AssetPath>/exists | /param | /graph | /flags | /slot
uefn://datatable/<AssetPath>/exists | /rows
uefn://opaque/<command>/<run_id>-<n>
```

Three nudges to one actor share a slot, so they collapse into one row and one
restore to the pre-run state. A facet is as fine-grained as the captured state:
setting two properties on one device shares `/settings` because the capture reads
the whole settings blob, and restoring it restores both. Opaque commands are the
exception and get a slot per call, because running a script twice is two events.

**A slot is never a file path.** Nothing may hand one to `basename`, a file
icon, an open-file handler or `FilesRevertedEvent.paths`. There is an explicit
filter with a test at each of those points.

## Adding capture to a command

1. **Classify it on the host.** Add a row to `EDITOR_OPS` in `editor_ops.py`
   with its `kind`, `slot` facet and revertability ceiling. The completeness test
   parses the listener tree, so a new command without a row fails the build.

2. **Write a `before` on the listener**, if the prior state is cheap to read.
   Return `{"targets": [...], "state": {...}}`, or `None` when there is nothing
   to capture. One or two property reads — never a level scan.

   ```python
   def _before_actor_label(params):
       actor = _find(params, "actor_path")
       if actor is None:
           return None
       return {"targets": [_actor_target(actor)], "state": {"label": actor.get_actor_label()}}
   ```

3. **Write an `inverse`**, returning the commands that put it back, in order.
   Return `None` rather than guessing: the change is still recorded, as `manual`,
   with your spec's `note` as the reason the user sees.

4. **Register it** in `CAPTURE`. For a command that creates something, supply a
   `created` callback instead of an inverse — the host's revert removes exactly
   what you name.

   ```python
   "set_actor_label": _Spec("actor", "label", _before_actor_label, _inverse_label),
   "spawn_actor":     _Spec("actor", "exists", None, None, _created_actor),
   ```

5. **Add a summary line** in `_summary` if the default (the command name with
   the underscores removed) does not read well in a timeline row.

Then run `pytest ducky_app/uefn_listener ducky_app/backend/workspace`. The parity
and completeness tests will tell you if the two tables disagree.

## Outcomes

Every entry carries `outcome`, defaulting to `ok`.

| outcome | Meaning | Recorded from |
|---|---|---|
| `ok` | It landed | the normal path |
| `blocked` | Refused by policy before anything changed | a lane denial, a listener refusal, the `ducky_revert_*` name guard |
| `failed` | Attempted, errored, nothing (or partly) applied | a raising `send_command`, a mutating tool returning an error |

Non-`ok` entries are excluded from revert, `index.json`, conflict detection,
`export_run.files` and every file count. They appear only in the timeline.

## Revert

`journal._revert_editor` handles an editor entry:

1. No inverse and nothing recorded as created → **manual**. Not an error: it
   comes back in the result's `manual` list with its reason, and the run reads as
   partly reverted rather than clean.
2. Otherwise, post the inverse steps and one `ducky_revert_creation` per
   recorded creation, bound as a revert run so the undo is itself attributed and
   itself revertable.

Within a run, entries revert newest-first, so an editor change made after a file
write is undone before it.

`ducky_revert_creation` is the single delete path and is not an agent tool. See
ADR 0002 §8 for its contract; `uefn_listener/listener/handlers/ducky_revert.py`
carries it in code, and `test_ducky_revert.py` covers every refusal.

## Opaque commands

`execute_python` and `exec_console_command` cannot be modelled, so they are
bracketed: a level snapshot before, another after, and the difference recorded
along with the code verbatim. Only the difference is stored — two full snapshots
per call would put megabytes of actor rows into the journal.

Actors that appeared become recorded creations, so a script that spawns forty
props is one Revert that removes exactly those forty. Actors that disappeared are
reported but never restored; a snapshot cannot put a deleted actor back.

Bracketing only happens in `full` capture mode, and a level above ~6000 actors is
refused rather than walked on the game thread — the change is still recorded,
with its code, and honestly says its effect is unknown.

## What this does not do

- **It is not undo.** Every revert is a compensating forward mutation. No editor
  undo API is used anywhere in the tree, and the change is already flushed to
  disk by the time anyone can click Revert.
- **Lanes do not apply.** Editor entries record `in_lane: null`. A lane *denial*
  is still recorded — that is the attempt being logged, not a lane check on an
  editor target.
- **Capture is best-effort.** `before`, `after`, the snapshot and every observer
  swallow their exceptions. A missing row is possible; a command broken by
  bookkeeping is not.
