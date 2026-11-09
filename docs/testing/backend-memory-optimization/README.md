# Backend memory and diagnostics validation

This change releases completed work and expired cache entries, narrows chat display
reads, and makes native-launcher memory diagnostics usable across restarts. Agent
transcript reads remain separate from the display projection.

The PR is based on `e1fbc1e1` (10 November 2026), after a rebase from `c18ed0d6`.
The existing native-child flag, startup stage events and clean-launcher-exit fixes
are retained. A locally tested 1.2.479 executable predates more than 120 upstream
commits; its packaged-runtime smoke result must not be presented as a build of this PR head.

Upstream meanwhile shipped its own shared-cache cap (`CACHE_MAX = 64`) and paged chat
loads (`_page_rows`, with the 0.5 s checkpoint flush wait). The rebase keeps both as
they are and layers this PR on top: byte budget and idle expiry on the cache, display
image references on the paged rows, and a one-row reader for `load_tool_result`.
This PR's own page reader is no longer used by `load_messages`: its flush could wait
30 s behind a busy writer, which upstream's checkpoint wait fixed.
Release versions and generated release assets are not changed by this PR.

## Changes and limits

- Shared UI cache: upstream's 64 entries plus an estimated 8 MiB; bridge response
  cache: at most 256 entries and an estimated 16 MiB. Oversized results are still
  returned, but not cached. Expiry runs while idle. Workers release their last
  function/arguments; remote input workers also release completed payloads.
- The existing 14 JS bridge workers are divided into control/urgent/background/
  blocking lanes (2/2/6/4). Stop, question responses and steering have their own
  control lane. Calls remain accepted under load; the input and HTTP lanes retain
  their existing behavior. No new agent-admission wait or concurrency cap is added.
- Agent-host writes use batches of at most 128 frames and normally 1 MiB. An
  individual frame may still use the existing 64 MiB transport allowance. Events
  retain FIFO order; queued/decoded payload references are released on completion
  or channel close. **This bounds batches, not the total accepted queue.**
- Chat pages use upstream's `_page_rows`. A cut tool result (`load_tool_result`)
  now reads only its row from an account-scoped snapshot, fetching encrypted message
  parts only for that row, instead of loading the whole chat. UI row IDs have room
  for large tool turns, and rewind uses the same stride. Full agent/export reads are unchanged.
- Displayed stored images use the existing authenticated attachment route. Sending
  or editing them resolves the stored reference back to complete image bytes, with
  ownership, containment, size and AI-ignore checks. Stored data is not discarded.
- Reading search status no longer loads an embedding model. Model loading when a
  search needs it remains available.
- The native backend starts the existing 30-second heartbeat after hello. For a
  native child, the census validates the launcher parent's name/start time and
  includes sibling WebView2 processes while excluding independent launchers.
  Numeric histories append to `crash/memory` under the app data directory: ten
  sessions, up to two 1 MiB chunks each. Rotation replaces older history, not app
  data. Disk errors do not stop heartbeat listeners. History is local and contains
  no chats, process command lines or other-program names.
- Queue/delivery diagnostics aggregate into two constant-space interval summaries
  outside the UI-stall buffer; reports stay capped at 200 records. This changes
  diagnostics, not actual event delivery or hidden-panel task lifetimes.

## Reproducible measurements

The included JSON files contain synthetic data only. Use the same interpreter and
dependencies in separate pristine/modified checkouts. The backend benchmark creates
isolated AppData and runs seven samples per chat size:

```text
python scripts/benchmark_backend.py --output backend-result.json
python scripts/benchmark_search_status.py search-result.json
node scripts/benchmark_perf_monitor.mjs e1fbc1e1
```

The first rows were measured on `c18ed0d6`. After the rebase, a probe of
`call_shared` on `542300bf` (unchanged in `e1fbc1e1`) vs this PR (2,000 unique keys, then 1.2 s idle) gives
**64 → 64** entries after the keys and **64 → 0** after idle. The chat page row is
gone: `load_messages` now uses upstream's reader on both sides.

| Same synthetic input (on `c18ed0d6`) | Upstream baseline | PR implementation |
| --- | ---: | ---: |
| Last completed worker payload retained | Yes | No |
| Diagnostic rows: 320 ms stall, then queue depths 1–290 | 200 | 2 |
| Serialized diagnostic JSON | 19,783 bytes | 187 bytes |
| Initial stall survives the diagnostic burst | No | Yes |
| Peak queue depth recorded | 290 | 290 |

Sources: [backend before](backend-before.json), [backend after](backend-after.json),
[diagnostic comparison](diagnostics-before-after.json). The new diagnostic summary
represents all 241 queue observations at or above the existing reporting threshold.

The raw backend files also contain timings and tracemalloc peaks. They are isolated
Python-call measurements on a machine running other work, not whole-application
RAM or end-to-end responsiveness results. The field named `p95_ms` is the maximum
of seven observations, not a well-estimated population percentile. Search status
did not load a model in the isolated probe; that fixture had no model directory
([result](search-after.json)). No global RAM, CPU or agent-throughput gain is claimed.

## Behavioral contracts

| Contract | Preservation boundary |
| --- | --- |
| C1: agents/conversations | Display projection is not a saved transcript; complete agent context and provider image bytes remain available. |
| C2: teams/plans/keeper | No admission quota, dependency change or keeper scheduling change. |
| C3: questions/permissions | No expiry, synthetic response or implicit approval; response calls have a dedicated control lane. |
| C4: background work | Visibility does not cancel agents, workflows, terminals or synchronization. |
| C5: targeted Stop | Existing run IDs/cancellation paths retained; control-lane and IPC tests cover dispatch. |
| C6: project writes | ProjectWriter, protections, encryption, journal and backup paths retained. |
| C7: UEFN/folder projects | Display reads resolve the conversation independently of the currently visible project. |
| C8: plugins/integrations | No plugin activation or registration removal; bridge cache eviction affects reusable responses only. |
| C9: windows/editor | Native callback ownership, dirty buffers and drafts are not evicted; launcher census covers sibling windows. |
| C10: local/shared data | Account checks apply to pages and attachment references; synchronization and full reads remain separate. |

Automated tests cover page equivalence, large single turns, complete context,
encrypted/account-isolated reads, image re-send, worker retention, idle expiry,
control dispatch, IPC batches, native startup, stale parent PIDs, history rotation,
disk failure and two independent history-writer processes. They do not replace a
prolonged interactive run with all external MCP servers, multiple providers and
multiple PCs. End-to-end Stop latency under GUI/HTTP saturation remains unmeasured.

## Validation on the PR base (`e1fbc1e1`)

- TypeScript (`tsc --noEmit`) and Ruff on the changed/new Python files passed.
- Full Python suite, one process (`pytest -q --maxfail=40`), completed:
  **7,636 passed, 27 skipped, 13 failed**.
- **11 of the 13 also fail on pristine `e1fbc1e1`** on this machine: three terminal
  tests need `ping` on the child PATH, one terminal test hits the console code page
  (`it’s` read back as mojibake), three bootstrap tests expect sibling provider-plugin
  repositories, the spell-check assertion expects lowercase `food` (this install
  returns `Food`), two native pane tests cannot create their test window, and the
  long nested path test hits the Windows path limit here.
- The other two (`test_panel_api_window::test_native_capture_refuses_fast_so_the_page_falls_back`,
  `test_verse_digests::test_a_new_assets_digest_is_found_without_waiting_for_the_memo`)
  failed only inside the full run and **passed 3/3 alone on both the PR and pristine
  `e1fbc1e1`**. The PR touches no capture, window or Verse digest file; the capture
  test found a real WebView2/monitor on this desktop. A full pristine-suite run was
  not done, so their order sensitivity is reported, not explained.
- The earlier full UI run (2,691 passed; one locale-dependent `SearchIndexCard`
  failure that also fails on the pristine base) was on `c18ed0d6` and was not
  repeated after the rebase. No new packaged EXE was built for the rebased PR.

[validation.json](validation.json) records the earlier runs on `c18ed0d6`.

Earlier local 1.2.479 validation included 257 Python tests, 15 UI tests and a
packaged-runtime smoke (MCP context cleanup and child-process worker teardown).
Its actual native session wrote 158 valid memory samples over 78.51 minutes with
up to two active agents. That supports the history mechanism, but is not a
matched-load old/new memory comparison or validation of this rebased executable.
Raw personal-session logs, absolute workstation paths, Epic assets and binaries
are deliberately kept out of this contribution.
