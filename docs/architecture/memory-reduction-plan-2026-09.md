# UEFN Ducky memory reduction: research and implementation plan

Research date: September 17, 2026. Source baseline: commit `45eb20e`, application version `1.2.135`.

Status: research plan with an implementation follow-up below. The research findings describe the original baseline. Measurement targets below remain proposed acceptance criteria, not achieved savings.

Further investigation of build 1.2.136 reproduced additional retention/allocation problems, implemented targeted fixes, and added a process-family sampler. See [Memory reductions that preserve functionality](memory-preserving-fixes-2026-09.md) for the measured synthetic results, 124-test validation, current process breakdown, and remaining packaged validation work.

## Implementation follow-up — September 17, 2026

The following changes are now implemented in the working tree:

- Main/focus windows and browser panes use a capability-checked WebView2 Low/Normal memory target when hidden or minimized/restored. Scripts remain running. Native subscriptions detach on close. Set `UEFN_DUCKY_LOW_MEMORY=0` to disable this policy for comparison or troubleshooting.
- Background jobs have a 64-slot admission bound, a 128-result retention limit, and a periodic reaper. Unconsumed results expire 120 seconds after completion, checked every 30 seconds. Cancelled queued work keeps its slot until dequeued so cancellation cannot create an unbounded executor backlog. Unknown cancellation IDs do not accumulate.
- CLI diagnostics retain at most 4,000 stdout and 8,000 stderr characters. Full stdout lines still reach the parser; the diagnostic retention limit does not truncate tool responses.
- Windows cancel/timeout/replacement targets the owned CLI process tree before falling back to the parent. Normal completion does not kill deliberately persistent background services. This does not merge legitimate IDE bridges or establish cleanup guarantees for already-detached children.
- Completed-chat snapshots have a 16 MiB estimated payload budget in addition to the existing 24-chat limit. Live snapshots keep their prior retention behavior; durable history and mounted conversations are not modified. Estimates include nested tool arguments/results and use weak memoization for immutable messages.
- Store, Appearance, Audio, and Skills/MCP Settings implementations load on demand, with a local loading fallback. The production build emitted four separate chunks totaling approximately 146 kB uncompressed; that figure is deferred code size, not measured RAM savings.

Validation: 69 focused Python tests (including a real Windows parent/child cancellation test and updater regression tests) and 63 frontend tests passed. Python lint and production TypeScript/Vite/build gates passed. The memory-target enum lookup was verified against the installed WebView2 managed SDK. A full Python run completed with 1,625 passes, two skips, and two failures: `test_retire_blender_nested_mcp` and `test_first_run_downloads_three_gateways_once`; both failures passed when rerun independently. The final job-queue refinement was verified by the focused run. Full-suite cleanliness is not claimed.

The suite initially could not collect because an existing updater edit had removed the `get_app_update_status` declaration; restoring that declaration preserved the surrounding new helper and restored collection. Other pre-existing workspace edits were retained.

No new runtime dependencies were added. The installed application was not replaced or restarted, and no user-owned running agents were stopped. A packaged-app memory benchmark and native hide/restore smoke test are still needed to quantify actual savings. Full history paging, general UI/event byte budgets and gap recovery, plugin import deferral, and a shared MCP backend remain proposed follow-up work.

## 1. Recommendation

Measure the complete Ducky process family, then ship small changes in this order: process ownership and cleanup; inactive WebView memory policy; bounded queues and abandoned job cleanup; paged chat history and byte-budgeted caches; deferred Settings and plugin implementation imports. Consider a shared MCP backend only after measuring the remaining cost of legitimate concurrent connections.

The main opportunity is repeated backend state plus retained UI state. The investigation does not establish a memory leak. Several useful optimizations already exist and should be preserved.

## 2. What the evidence shows

### Screenshots supplied by the user

| Visible row | 17:01:30 screenshot | 17:02:36 screenshot |
|---|---:|---:|
| Ducky app | 159.3 MB | 158.3 MB |
| MCP bridges | 93.7 MB, one row | 281.8 MB, three rows |
| WebView: Ducky | 132.9 MB | 139.0 MB |
| Sum of visible Ducky rows | 385.9 MB | 579.1 MB |
| Cursor | 2,475.5 MB | 2,501.3 MB |
| System memory utilization | 72% | 73% |

The visible Ducky increase is 193.2 MB. Of that, 188.1 MB is accounted for by two additional bridge rows. This explains most of the difference without assuming a leak. The screenshots do not expose every browser helper or establish whether bridges were unnecessary. The system percentage includes all applications and Windows.

### Read-only live inspection during the preceding investigation

One sample contained the following private committed memory, rounded to MiB:

| Ducky component | Private committed memory |
|---|---:|
| Main application | 185.0 MiB |
| Browser process | 41.8 MiB |
| Renderer | 179.6 MiB |
| GPU process | 128.4 MiB |
| Network, storage, and crash helpers | 34.0 MiB |
| Four Python bridges | 388.4 MiB |
| Total of those processes | 957.2 MiB |

The bridge parents were Cursor, Codex, and two children of one Claude process. Counts changed between samples. This is evidence of multiple connections, not proof of duplicate configuration or orphaning. The figure excludes the clients' own memory and any unclassified descendants; it is not an idle benchmark.

Private bytes measure private committed allocations, including memory that may not currently be resident. Working set includes shared pages, so adding working sets can double-count shared memory. Neither should be compared directly with screenshot totals as if they were the same metric. Measure private working set separately for resident private RAM. See [Windows working sets](https://learn.microsoft.com/en-us/windows/win32/procthread/process-working-set) and [Windows memory counters](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex).

### Verified source findings

Repository paths in this document are relative to the repository root.

| Finding | Evidence | Interpretation |
|---|---|---|
| Each bridge imports core tools and loads enabled plugin backends | `ducky_app/frontend/launcher.py`, `run_bridge()`; `ducky_app/backend/uefn_plugins/host.py` | Repeated processes duplicate Python objects and plugin registrations. Async loading improves responsiveness but does not eliminate retained imports. |
| Run identity deliberately makes bridge arguments unique | `ducky_app/backend/agent/coding_agents/mcp_inject.py`, `stamp_mcp_identity()` | Removing `--ducky-run-id` to force reuse risks attributing edits to the wrong run. |
| Main window close hides the window | `ducky_app/frontend/ui_web/webview_app.py`, `on_hide()` and `on_closing()` | No explicit `MemoryUsageTargetLevel` or `TrySuspendAsync` handling was found. Hiding is not application shutdown. |
| Cancellation paths directly kill the CLI process | `ducky_app/backend/agent/coding_agents/proc_exec.py` | Child-process ownership needs testing. This alone does not prove children survive. |
| UI dispatch queues have no maximum | `ducky_app/frontend/ui_web/ui_dispatch.py`, `_Channel` | A stalled `evaluate_js` consumer can retain queued strings and window references. Actual retained bytes are unmeasured. |
| Event replay is bounded by 4,000 events, not bytes | `ducky_app/frontend/ui_web/panel_httpd.py` | Large event payloads can make the bounded ring expensive. |
| Abandoned completed jobs are pruned on the next job start | `ducky_app/frontend/ui_web/bridge_jobs.py` | An unpolled completed result can stay reachable indefinitely if no subsequent job starts. Current age is measured from submission, not completion. |
| Chat cache is capped at 24 conversations | `ducky_app/frontend/ui_web/web/src/hooks/chatMessagesCache.ts` | Entry count is bounded; aggregate retained message size is not. |
| Chat hydration requests the full conversation | `ducky_app/frontend/ui_web/web/src/hooks/useChatMessages.ts`, `load()` | Virtualized rendering does not prevent full history from occupying the JS heap. |
| Paging API constructs full UI rows before slicing | `ducky_app/frontend/ui_web/panel_api_chats.py`, `load_messages()` | Using its existing parameters can reduce response size, but backend peak allocations also need work. |
| Settings and its sections use eager imports | `ducky_app/frontend/ui_web/web/src/App.tsx`; `views/SettingsView.tsx` | Deferral is a candidate for startup savings, not a promise that memory disappears after first use. |

Installed Anthropic plugin code also uses `--mcp-config` with `--strict-mcp-config`. Therefore, adding that flag is not a new fix. Any Claude changes belong in the plugin source repository, not the installed AppData copy. Claude documents strict config behavior and scope precedence in its [MCP reference](https://code.claude.com/docs/en/mcp). The actual launch arguments and client version still need checking in a controlled reproduction.

### Optimizations already present

- Monaco uses dynamic imports in `verse-editor/monaco/setupMonaco.ts`.
- Chat uses `VirtualChatMessageList.tsx`; hidden chat/file tabs are unmounted in `SplitEditorLayout.tsx`.
- `FileEditorSessions.retain()` disposes models belonging to closed file tabs, and the provider calls it.
- Main WebView storage uses a stable profile directory.
- Browser overlay tabs already share an environment within their browser profile and dispose controls on close. Their profile is separate from the privileged application UI; preserve that separation.
- Agent push events are batched/coalesced and delivered through the HTTP event bus. They do not all go through the JS dispatch queue.
- Terminal replay has a 400-chunk ring, performance tracing has a 2,000-entry ring, and several caches already use bounded maps.
- The actual packaging configuration in `build/unified.spec` uses `COLLECT` and separate role executables sharing bundled files. Some old comments still describe one-file packaging. Changing to one-directory packaging is not a new optimization.

## 3. Phase 0 — establish a repeatable baseline

**Deliverable:** an opt-in memory diagnostics command and a small benchmark report. Estimated effort: 1–2 engineering days.

Extend `frontend/perf_trace.py` or add a separate diagnostic sampler; do not turn expensive heap tracing on for every normal launch.

Collect every five seconds during benchmarks:

- App/build version, Windows version, WebView runtime and managed SDK versions, plugin IDs/versions, workload and window visibility.
- PID, creation time, parent PID, process role, connection/run correlation, private bytes, private working set, total working set, handle/thread counts, and CPU delta.
- Browser membership from the owning WebView environment/process information; parent/command-line classification is a diagnostic fallback. Exclude unrelated SearchHost WebViews.
- Ducky-owned CLI and nested MCP children as a separate subtotal. Show external IDE memory separately.
- Chat cache entry/estimated-byte counts, editor model counts, live windows/panes, queue bytes/depth, pending/completed jobs, and event ring bytes.

Reuse process handles where practical. Redact command arguments, prompts, credentials, environment values, and file contents from exported diagnostics. A normal support report needs metadata, not conversation text.

Use an isolated test profile and synthetic conversations. Run the same release build configuration, project fixture, and plugin set before and after each change. Capture at least three runs per scenario:

| Scenario | Measurement |
|---|---|
| GUI only, fresh profile | Cold launch, ready state, then five minutes idle |
| One bridge with GUI closed | Baseline cost of the IDE integration |
| GUI plus one/three active clients | Incremental bridge cost and startup peaks |
| Hidden/minimized/restored | Five-minute inactive period; 30 restore cycles |
| Large history | Open 30 synthetic chats, including long tool results; return to first chat |
| Editor/browser churn | Open/close 50 files and 20 browser/focus windows; preserve dirty files |
| Agent lifecycle | 30 normal runs, cancellations, reconnects, and client exits |
| Slow consumer | Pause event consumption; flood synthetic events; resume and reconcile |
| Soak | 60 minutes idle plus a repeatable workload cycle |

For Python, compare opt-in `tracemalloc` snapshots after equivalent workload checkpoints. It cannot account for all .NET, browser, GPU, or native allocations. Use Edge heap snapshots and retained-object paths for renderer growth; use Windows tooling for native growth. Profiling overhead must be recorded. See [Python allocation tracing](https://docs.python.org/3/library/tracemalloc.html) and [Edge heap snapshots](https://learn.microsoft.com/en-us/microsoft-edge/devtools/memory-problems/heap-snapshots).

**Exit criteria:** reproducible median/peak private bytes and private working set, per-client incremental cost, and a known workload for each proposed fix. No claim of a leak based on a single peak or on failure to return exactly to cold-start memory.

## 4. Phase 1 — process ownership and inactive UI

### 1A. Explain and clean up bridge lifetimes

**Files:** `frontend/launcher.py`, `backend/agent/coding_agents/proc_exec.py`, `mcp_inject.py`, `mcp_bridge_host.mjs`; client adapters in their plugin repositories.

1. Record connection start/end, parent creation identity, run correlation, clean EOF, and shutdown reason in bounded diagnostic metadata.
2. Reproduce two bridges under one Claude parent. Distinguish separate legitimate sessions, reconnect overlap, client-internal workers, and a child surviving a completed run. Inspect sanitized effective config names/commands and the installed adapter version.
3. Give Ducky-spawned CLI trees explicit lifetime ownership. Prototype Windows Job Objects with kill-on-close, child inheritance, and creation-time race prevention. Test nested-job compatibility and handle assignment failures explicitly.
4. Use graceful shutdown where the client supports it, then a bounded forced cleanup for owned processes. Check normal completion as well as cancel, timeout, replacement, and app exit.
5. Keep externally owned IDE bridges tied to their stdio connection. Never terminate every process named Ducky Bridge, and never use a fixed idle timeout to kill a legitimate long-running tool call.

Windows Job Objects can manage related processes as a group; use this for ownership rather than hard RAM limits. [Microsoft Job Objects documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

**Acceptance:** after a test-owned connection or run closes, its owned descendants exit within a proposed ten-second cleanup budget; unrelated clients continue working. After 30 cycles, process count returns to the expected baseline. If duplicate connections are legitimate, mark this investigation complete without fabricating a deduplication fix.

**Potential benefit:** approximately 97 MiB of private commit per avoidable bridge in the observed sample, plus any wrapper/child overhead. This is conditional; it is not a guaranteed resident-RAM saving.

### 1B. Introduce an inactive WebView memory policy

**Files:** `frontend/ui_web/webview_app.py`, `focus_windows.py`, `browser_overlay.py`, `webview_recover.py`, `win_frameless.py`; proposed new `webview_memory.py`.

First implement a capability-checked Low/Normal policy for inactive versus visible views. Run native property changes on the form UI thread. Cover hide-to-tray, minimize, restore, focus-window close, overlay tab changes, startup, and renderer replacement. Treat unsupported APIs as a logged no-op, not a startup failure.

The inspected development environment has pywebview 6.2.1 and WebView2 managed DLLs 1.0.3856.49; the running browser reported 153.0.4234.32. Verify the DLLs actually shipped in the installer, because browser runtime and managed SDK are separate dependencies.

Low memory target keeps scripts running and is best effort. Microsoft advises choosing either Low/Normal or Suspend/Resume for a WebView rather than mixing the two policies. See the [MemoryUsageTargetLevel contract](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.memoryusagetargetlevel).

Keep full suspension as a later experiment. It requires controller invisibility and can pause scripts. Before enabling it, implement resume reconciliation, event-gap handling, and exclusions for voice/WebRTC, browser automation, pending user questions, UI RPC, and other renderer-dependent work. Pending native operations must not block the UI thread. See [TrySuspendAsync](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.trysuspendasync).

**Acceptance:** 30 hide/restore cycles without black windows, missing events, lost drafts, broken voice, or blocked UI RPC. Compare five-minute hidden private working set against the same build without the policy. Initial success target: at least 15% lower WebView private working set under the defined fixture, without claiming private-commit reduction from paging alone. Restore target: p95 under 500 ms on the reference machine; investigate any regression even if memory falls.

## 5. Phase 2 — bound retained work and conversation data

### 2A. Queue limits and abandoned jobs

**Files:** `ui_dispatch.py`, `panel_httpd.py`, `panel_api.py`, `bridge_jobs.py`, `agent_modes.py`, `terminal/session.py`, and JS event consumers.

- Add byte accounting to event history and pending delivery, not just entry counts. Proposed experiment: 8–16 MiB per event replay ring, alongside the existing count limit; calibrate from normal traffic.
- Add an explicit oldest-sequence/gap response. A consumer falling behind must reload authoritative state rather than silently treating dropped events as a complete stream.
- Coalesce replaceable status snapshots by run/window. Preserve ordered tool lifecycle events, approvals, completion state, and terminal semantics. Persist durable results before evicting transient copies.
- Replace unbounded accumulation in stalled JS dispatch channels with bounded admission and typed behavior. Do not drop arbitrary JS strings or block producers indefinitely with a naive bounded queue. Separate replayable notifications from required request/response work; required operations need completion/error paths.
- On window destruction, cancel pending work and release references. A stop sentinel behind a permanently blocked call cannot itself unblock that call; prevent new accumulation and make the limitation visible.
- Reap completed, unconsumed jobs periodically using completion timestamps. Preserve a retrieval grace period, and avoid expiring a long job immediately because its submission was old. Bound queued job admission; six executor workers do not bound the executor's pending queue. Reclaim unknown cancel IDs and result references.
- Measure terminal chunk sizes before changing the existing ring. If needed, use a byte budget while preserving complete character/escape sequences and replay behavior.

**Acceptance:** synthetic oversized events and abandoned results stay within configured retention budgets; paused clients resynchronize correctly; existing fast-job polling, cancellation, and event ordering behavior still passes. No monotonic queue growth during a 60-minute stalled-consumer fixture. Do not claim these paths caused the screenshot usage without a retaining-path measurement.

### 2B. Paged chat history and weighted cache eviction

**Files:** `web/src/hooks/useChatMessages.ts`, `chatMessagesCache.ts`, chat reducer modules, `components/VirtualChatMessageList.tsx`, `panel_api_chats.py`, `project_chats.py`, and the conversation store/repository implementation.

1. Start with the newest complete turns, using the existing `load_messages` parameters where safe. Experiment with roughly 100–200 UI rows, but avoid splitting a tool exchange or activity group without explicit continuation handling.
2. Add older-history loading on scroll. Preserve scroll anchors, edits, tool grouping, active streaming, and navigation to older messages. Search/export must still reach full stored history.
3. Add a conversation revision to paging or adopt stable message identifiers. Current UI row IDs are positional; editing/rebuilding earlier history can invalidate an outstanding page cursor.
4. Change backend paging so it avoids constructing every UI row first. Profile store decoding too: slicing rows after loading one giant conversation JSON still leaves a backend peak. If necessary, propose a store migration separately, with rollback and compatibility tests.
5. Add an estimated retained-byte budget alongside the 24-chat limit. Initial experiment: 16–32 MiB for inactive chat snapshots. Treat this as a controllable estimate, not an exact V8 heap measurement. Avoid repeatedly JSON-serializing whole conversations merely to measure their size.
6. Prefer evicting inactive clean snapshots. Persist drafts and durable run state; active conversations also need a bounded display window so pinning them does not defeat the budget. Keep large artifacts out of duplicated inline strings where the current payload contract allows references.

**Acceptance:** a 10,000-message fixture opens with a bounded first page; older history loads without jumps, duplicates, missing results, or lost edits. Backend conversion work scales with the requested page where the storage layout permits it; explicitly report any remaining full-document decode. Opening 30 chats respects the inactive cache budget and does not truncate stored conversations or change model context.

## 6. Phase 3 — reduce initial and per-bridge imports

### 3A. Defer Settings sections

**Files:** `web/src/App.tsx`, `views/SettingsView.tsx`, `views/settings/*`, and their callers in the workspace layout.

Use dynamic imports and local Suspense boundaries for heavy Settings sections, beginning with Store, Appearance, Audio, and other measured contributors. Check the full import graph: changing one import is ineffective if another eager path still imports the same implementation. Keep shared small utilities separate from large views.

Do not preload every deferred section immediately after launch. Preserve deep links, onboarding, plugin settings, keyboard navigation, error recovery, and focus-window behavior. Lazy imports improve first-use cost distribution; imported modules generally remain loaded afterward.

**Acceptance:** build output and runtime network traces show unused sections are not on the boot path; first opening each section works without blank panels. Compare cold-start renderer heap and time-to-ready with three matched runs. Keep a change only when it has a measurable benefit without an unacceptable first-open delay.

### 3B. Make plugin registration lighter

**Files:** `frontend/launcher.py`, `backend/tools/__init__.py`, `backend/uefn_plugins/host.py`, `backend/bridge/plugin_gate.py`, and affected Store plugin source packages.

Profile allocations at interpreter start, core MCP import, core tool registration, plugin registration, and first real tool use. Rank modules by measured allocation contribution. Defer provider SDKs and optional implementations until their features run, while keeping lightweight capability metadata available.

Do not blindly skip enabled plugins in bridge mode. Some supply required MCP tools, nested servers, settings, or registration hooks. Separate metadata and implementation where possible; preserve schemas and tools/list behavior. Each deferred implementation needs a thread-safe first-load path, failure reporting, and correct refresh after plugin enable/update.

**Acceptance:** identical available tool names/schemas for the same enabled plugin set, successful first calls, no registration races, and lower steady idle private bytes per bridge. Test the packaged executable as well as source execution. No SDK upgrade or packaging rewrite is required merely to attempt these changes.

## 7. Phase 4 — optional shared MCP backend

Proceed only if legitimate concurrent bridges remain a major share of measured memory after earlier phases. This is the largest architectural change and should have its own ADR and prototype.

Preferred prototype: keep one small stdio adapter per client connection, and forward to a shared local backend through authenticated, user-restricted IPC. A Windows named pipe can preserve the current external stdio protocol without requiring all clients to migrate to HTTP. The adapter must actually be lightweight; a renamed Python bridge that still imports every plugin saves little.

The backend must run when the GUI is closed. Key backend sharing by compatible app/plugin version and project/listener configuration, or make those settings explicitly request-scoped. Do not introduce one global backend that silently shares process-global settings between incompatible projects.

Required design work:

- Preserve run, conversation, profile, project, lane restrictions, and writer attribution for every request. Bind context per request and clear it afterward; never set process-global environment variables to impersonate the latest caller.
- Namespace JSON-RPC IDs by connection and route progress, cancellation, resources, roots, and server-to-client requests to the correct client.
- Reference-count connections and work; reclaim backend state only after the last relevant connection and operation completes. Do not interrupt running tools when a different client disconnects.
- Define schema refresh, nested MCP ownership, restart behavior, version negotiation, connection admission, and queue byte limits.
- After a crash, report uncertain mutating calls rather than replaying them automatically. Retain a feature-flag fallback to today's dedicated bridge model.

If choosing HTTP instead, test every supported IDE and SDK version explicitly. The pinned local `mcp==1.28.1` declares protocol version `2025-11-25`. The current July 2026 Streamable HTTP revision has different lifecycle, session, and cancellation semantics. Do not build a plan around `Mcp-Session-Id` as a universal current mechanism or upgrade the protocol incidentally. Sources: [2025 transport contract](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [2026 Streamable HTTP contract](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).

**Decision gate:** a prototype with three clients must show at least 25% lower total bridge/backend private bytes than three dedicated bridges, counting adapters, daemon, wrappers, and nested MCP children. This is a proposed investment threshold, not a prediction. It must also pass concurrent attribution, cancellation, different-project, reconnect, GUI-closed, and crash-recovery tests. Keep the dedicated model if these conditions are not met.

## 8. Delivery sequence and reviewable changes

Estimates assume one developer familiar with this code; they exclude release scheduling and may overlap. Validate Phase 0 before committing to a total savings target.

| Change | Priority | Estimated effort | Depends on | Main benefit |
|---|---|---:|---|---|
| A. Diagnostics and benchmark fixtures | P0 | 1–2 days | Nothing | Reliable attribution and regression evidence |
| B. Bridge lifetime investigation and owned-tree cleanup | P1 | 1–3 days | A | Removes avoidable processes if reproduced |
| C. Low/Normal WebView policy | P1 | 1–2 days | A | Lower inactive resident memory |
| D. Queue budgets, gap recovery, completed-job reaping | P1 | 2–4 days | A | Prevents retained work from accumulating |
| E. Chat paging and weighted caching | P1 | 3–5 days | A; D's reconciliation contract | Lower long-session heap and payload peaks |
| F. Settings lazy loading | P2 | 1–2 days | A | Lower cold-start UI cost |
| G. Measured plugin import deferral | P2 | 2–4 days | A | Lower per-process baseline |
| H. Shared-backend ADR and prototype | P3 | 3–5 days for prototype | B and G measurements | Reduce cost of legitimate concurrent clients |

H is not a production delivery estimate. Full rollout requires the compatibility and recovery work identified above. Full renderer suspension should likewise remain experimental until D's resynchronization is reliable.

## 9. Verification, rollout, and completion criteria

Use feature switches for memory policy, paging, and any shared backend. Roll out one behavioral change per PR, with the exact before/after workload, versions, median/peak counters, and regressions reported. Preserve existing settings on rollback; cached data eviction must never delete durable conversations or unsaved edits.

Existing tests to extend or run as relevant:

- `ducky_app/frontend/test_bridge_startup.py` and `test_mcp_block_bridge.py`.
- `ducky_app/backend/agent/coding_agents/test_mcp_inject.py` and `backend/workspace/test_identity.py`.
- `ducky_app/frontend/ui_web/test_bridge_jobs.py`, `test_panel_push_batch.py`, `test_browser_overlay.py`, and `test_webview_recover.py`.
- `web/src/components/VirtualChatMessageList.test.tsx` and `web/src/verse-editor/fileEditorSessions.test.ts`.
- Add Windows integration tests for child ownership and the native visibility policy; mocks alone cannot validate WebView memory behavior.
- Add functional paging/revision/scroll and event-gap tests. Use synthetic content; do not capture user conversations as fixtures.

Run relevant Python tests with the project environment, frontend tests/type checking/build for UI changes, and packaged smoke tests for process/native changes. Follow the repository's full release checks before shipping. The plan itself does not require running the application test suite because it changes documentation only.

Completion means:

1. Every measured Ducky process is classified and legitimate connections have defined ownership.
2. Repeated cancelled/completed runs leave no test-owned children behind.
3. Hidden views reclaim measurable resident memory without breaking restore or background behavior.
4. Chats, results, and delivery queues have explicit size/lifetime controls with reliable recovery.
5. A warm 60-minute fixture shows no unexplained monotonic retained-object/process growth. Use a proposed 5% post-warm-up private-byte drift band as a triage threshold, not a universal proof of leak freedom; investigate excursions with retained-object evidence.
6. Results show both memory and responsiveness. Paging that lowers RAM while causing freezes does not pass.

## 10. Practical actions and approaches to avoid

For immediate use, disable Ducky MCP only in IDEs where its tools are not needed, finish unused agent sessions, close unnecessary browser/focus/file tabs, and use the tray's Exit action when the GUI and its background tasks are no longer needed. Confirm affected work is idle first. Disabling a needed client connection is a functionality tradeoff, not a code optimization.

Do not implement periodic forced garbage collection, working-set trimming, arbitrary process memory caps, repeated page reloads, disabled GPU acceleration, deletion of browser profiles, or removal of run identity as a memory fix. These can move the cost, lose state, or break behavior without addressing retained objects. Keep the privileged app profile separate from arbitrary browsing content.

Microsoft's January 2026 [WebView2 performance guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/performance) supports deferring heavy content, reusing suitable environments, disposing unused controls, and measuring real workloads. Ducky already implements parts of that guidance. The next work should target the verified gaps above, with measured savings attached to each change.
