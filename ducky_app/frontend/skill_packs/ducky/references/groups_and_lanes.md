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

## Make a swarm

Make a swarm: write the jobs from the request → `ducky_swarm_create(goal, jobs)` → answer the
one card (agents, size limits, detail, models) → preview → Build. Never seat a whole team by hand.

**Team = one `ducky_swarm_create` call (HARD).** It validates first and builds all seats or
none; never hand-chain `ducky_group_create` / `ducky_spawn_chat` / `ducky_group_set_lane`
(hand-built swarms left half-made groups and seats without lanes). Example:

```python
ducky_swarm_create(goal="Shop island", jobs=[
    {"name": "Verse: shop logic", "purpose": "gameplay", "task": "Buy and sell",
     "steps": ["Buy and sell device"], "paths": ["Content/Verse/Shop/**"]},
    {"name": "UI: shop HUD", "purpose": "gameplay", "task": "Shop panel",
     "steps": ["Shop panel widget"], "paths": ["Content/UI/Shop/**"]},
])
```

1. First write every job from the person's request, each `{name, purpose, task, steps[],
   paths[], kind?, model?}`: a game might get Narrative, Modeling, Texture, Research; a movie
   Script, Storyboard, Editing. Then call it **without `detail`**, so the question card asks
   it: never pick the detail level yourself.
2. `status: needs_answers` → ask its `questions` with ONE `ducky_ask_user` (how finely to
   split the work, how many agents, the size limits, a model per job), then call again with
   `detail`, `agents`, `max_agents`, `per_group` and each `jobs[i].model`. These are asked
   every time a swarm is made. `detail`: 1 Areas (one agent per area), 2 Jobs (suggested),
   3 Parts (a job splits into its parts, e.g. Modeling → blockout, hero props, set dressing),
   4 Parts in sub-groups (each split job becomes a sub-group of its parts). Each option
   says how many agents it makes; the size limits still cap it.
3. `status: needs_confirm` → show the preview card: the tree, every seat's steps
   (`preview.plan`) and a detail slider (`levels`). Let the person move the slider and look
   before Build; they build it as is (same call + `confirm=true`) or change it: another
   `detail`, or `edits=[{drop}, {rename, to}, {seat, model}, {seat, add_step},
   {seat, drop_step}, {seat, rename_step, to}, {mute}|{unmute}]`; each returns a new preview.
   Build makes exactly the previewed sub plans and steps.
4. On confirm it builds plan-first: a root group named after the goal with you as lead, one
   sub plan per job with its steps, then the groups and seats (write lanes from `paths`),
   then assigns each sub plan to its seat, which starts it.

Tree rules: `auto` = one agent per job, capped at the most agents in the swarm (jobs over the
cap merge into a related job, never dropped). When the lead has more than `per_group` seats,
related jobs nest into `<Area>: <purpose>` sub-groups (Modeling + Texture → `Art: assets`),
at most 3 levels deep. Tester and Reviewer stay directly under the lead. Seats are named by
role (`Verse lead`, `Tester`). Every sub-group starts muted; the person can unmute any.

Each build point's Done when ends with: the change and its tests are written; commit your
files in your own `sandbox_worktree` of `main` and push to main (no branches:
`git pull --rebase origin main`, then `git push origin HEAD:main`). Build points run no
test suites, builds or live panels and never wait on another agent; the plan's last section
is a test phase whose `after` lists every build step, and it runs everything once.

A new plan with 3+ sections or 10+ steps whose Files don't overlap comes back with a
`swarm_suggestion`: offer it with one `ducky_ask_user` ("Run this as a swarm of N?") instead
of working alone.

## Delegate: reuse first, add second, recycle when bloated

1. `ducky_list_duckies`: pick the specialist by `when_to_use`.
2. A whole team is a swarm (`ducky_swarm_create`, above). One helper for your own work:
   `ducky_group_create(name)` (reused if it exists), then
   `ducky_group_add_member(group_id, <your chat id>, as_leader=true)` to lead it.
   Nest a group inside another (`ducky_group_create(name, parent_folder_id=…)`) only for
   a sub-team with its own lead; one more helper joins your own group instead.
3. **Reuse:** `ducky_group_members(group_id)` (or `ducky_agent_list` →
   `my_group_members`). If that specialist is already seated for this work, follow up
   with `ducky_send_chat_message(conv_id, message)`; don't add a duplicate.
4. **Add:** `ducky_spawn_chat(ducky=…, message=…, group_id=…)` seats the ducky and sends
   the first message (it waits for the reply by default). `group_id` is optional:
   without it the member joins your own group (the one you are in, else one named
   after this chat). Or
   `ducky_group_invite(group_id, ducky)` to seat without a message.
5. **Recycle** a member whose context is too long or confused:
   `ducky_recycle_member(conv_id, continue_message=…)` writes a full handoff, removes
   it and seats a fresh twin in the same group that continues from the handoff.

Delegate when a task clearly fits another ducky's specialty, not for every small step.
For multi-step or multi-ducky work make a plan first (`ducky_create_plan`); leaders
give each member its own plan before handing work over.

## How groups are organized

- **One group per job.** Your helpers join the group you are in or lead. Never make a
  group just to hold one ducky, and never leave an empty one.
- **Workflows you run** (`run_workflow`): an Agent step's ducky joins your group. Run from a
  chat with no group, or from the Run button or a schedule, it joins one group named after
  the workflow (beside that chat, else at the top level), made when an Agent step first
  runs and reused every run. A run never makes a nested or empty group.
- **Before you make a group,** look for one that already does this job
  (`ducky_list_chats`: groups and their `parent_conv_id`) and reuse it.
- **When the job is done,** archive the whole group; its members and nested groups go with it.

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
- **Changesets.** `changeset_list(group_id=…)` shows what every member wrote,
  members of nested groups included (archived runs too: `archived=true`,
  `revert_locked=true`). Summaries include
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
