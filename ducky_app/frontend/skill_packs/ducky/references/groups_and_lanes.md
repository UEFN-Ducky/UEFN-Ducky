---
description: "Working with other duckies: group swarms, reuse and recycle members, write lanes, changesets, agent-to-agent messages"
metadata:
  order: 7
  label: "Groups, lanes and messages"
  default_enabled: false
  load_condition: "Delegating to another ducky, leading or joining a group swarm, splitting parallel work, or messaging another agent"
---

# Groups, lanes and messages

Each ducky is a saved profile (skills, tools, model, personality) with a
`when_to_use` hint. Other duckies work for you as **members of a group swarm**;
there are no separate subagents.

## Delegate: reuse first, add second, recycle when bloated

1. `ducky_list_duckies`: pick the specialist by `when_to_use`.
2. A swarm: `ducky_group_create(name)` (reused if it exists), then
   `ducky_group_add_member(group_id, <your chat id>, as_leader=true)` to lead it.
   Nest a group inside another with `ducky_group_create(name, parent_folder_id=…)`.
3. **Reuse:** `ducky_group_members(group_id)` (or `ducky_agent_list` →
   `my_group_members`). If that specialist is already seated for this work, follow up
   with `ducky_send_chat_message(conv_id, message)`; don't add a duplicate.
4. **Add:** `ducky_spawn_chat(ducky=…, message=…, group_id=…)` seats the ducky and sends
   the first message (it waits for the reply by default), or
   `ducky_group_invite(group_id, ducky)` to seat without a message.
5. **Recycle** a member whose context is too long or confused:
   `ducky_recycle_member(conv_id, continue_message=…)` writes a full handoff, removes
   it and seats a fresh twin in the same group that continues from the handoff.

Delegate when a task clearly fits another ducky's specialty, not for every small step.
For multi-step or multi-ducky work make a plan first (`ducky_create_plan`); leaders
give each member its own plan before handing work over.

A member can run on an external coding agent: `ducky_spawn_chat(…,
coding_agent="claude_code" | "codex" | "cursor")`, or automatically when the
profile's favorite model names one. It keeps one session per chat, so follow-ups
remember everything.

## Write lanes and changesets (parallel members)

Every project write goes through one pipeline: it is attributed to the chat and run
that made it, ledgered per run, and checked against the writer's lane.

- **Lanes.** The leader splits the work into disjoint write lanes **before** parallel
  members start: `ducky_spawn_chat(…, group_id=…, write_allowed=["Content/Verse/Shop/**"])`,
  `ducky_group_invite(group_id, ducky, write_allowed=[…])`, or later
  `ducky_group_set_lane(group_id, member_conv_id, write_allowed=[…])`. Globs are
  gitignore-style (`**` spans folders; a bare folder means `folder/**`); `[]` is
  read-only. Overlapping lanes are refused. `ducky_group_get_lanes(group_id)` shows the
  map. Keep shared files such as `module_declarations.verse` in the leader's lane.
- **Members can't change their own lane.** An out-of-lane write is refused (only
  flagged while the `write_lanes_mode` setting is `shadow`): never retry the path; ask
  the leader in the group chat or stay inside the lane.
- **Changesets.** `changeset_list(group_id=…)` shows what every member wrote
  (archived runs too: `archived=true`, `revert_locked=true`). Summaries include
  `source` (agent / user / revert) and `programs` (`file`, `uefn`, `blender`, …). The
  ledger covers UEFN listener and Epic `unreal__*` changes, Store plugin edits, and
  human file edits (`source=user`, `run_id` like `human:YYYY-MM-DD`, `tool=external`
  for Explorer / VS Code / UEFN). Review it before `workspace_compile_verse`.
  `changeset_export` / `changeset_contents` work on archived runs. `changeset_revert`
  works only on live agent runs; agents can't revert human runs.
  `changeset_archive(run_id)` locks revert without deleting the log.

## Agent-to-agent messages

If a spawn times out the member is **not** dead: its reply arrives later in your chat
as a `[ducky:agent-message]` turn (matched by `response_id`). Never spawn it again.

- `ducky_agent_list`: live agents (chats) you can message: id, backend, running.
- `ducky_agent_send(to=…, message=…, expect_reply=true)`: returns a `response_id`; then
  finish your turn. The reply, or a notice (`turn-ended`, `errored`,
  `awaiting-input`), arrives as a new `[ducky:agent-message]` turn. To answer someone,
  echo their `response_id` with `expect_reply=false`.
- `ducky_agent_inbox`: re-read your recent messages in full.
- `ducky_agent_transcript(conv_id)`: read a peer chat's history.
- `ducky_agent_stop(conv_id, cascade=…)`: stop a runaway agent (and its members).

External coding agents pass their own chat id as `sender=` / `conv_id=` (it is in
their system prompt); embedded duckies may leave it out.
