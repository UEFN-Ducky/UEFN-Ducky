# Memory reductions that preserve functionality

September 17, 2026. Source comparison: `b013c4e` (1.2.136) versus the working-tree changes described here. The installed application remains 1.2.136; these follow-up fixes have not been packaged or installed.

## Findings and implemented fixes

| Problem reproduced | Fix | Preserved behavior |
|---|---|---|
| An idle UI dispatch thread retained its most recent script or callback closure, including any captured window. | Release the completed payload before waiting for another item. | Every live-window operation still executes in order. |
| Native close of a detached focus window did not stop its dispatcher. A stop marker also left queued payloads pinned if a native call was stuck. Late producers could recreate a destroyed window's worker. | Clean up on the actual `closed` event, drain pending work for that destroyed window, reject late submissions, and track closed windows with weak references. | Closing still returns tabs to the main window. Other windows and the shared operations worker continue normally. Minimize/hide does not dispose a window. |
| The file-path cache kept a full listing for every changing Content-directory timestamp. | Keep the latest listing and rebuild on a different key. Replace the cache without modifying lists already held by callers. | The full current file list and cache hits remain available; no files are deleted. |
| Three file-type checks read a whole file before slicing its first 4,096 bytes. | Read only the 4,096-byte header. | Classification examines exactly the same prefix. Save validation, binary protection and atomic writes remain intact. |

These changes do not disable a bridge, editor, plugin, browser feature, language service, or saved history. They do not alter AI context or conversation storage. Other concurrent changes to context-management files are outside this work.

## Verification

Before the fixes, 11 new regression cases failed and one passed. After the fixes and an additional repeated-close test, 124 focused Python tests passed. The run covered file classification/read/write behavior, binary protection, media handling, native chrome, browser overlays/recovery, focus-window tab return, memory-policy behavior, event batching, MCP startup, bridge blocking and MCP identity injection. Python lint and whitespace checks passed.

The new lifecycle tests include 30 close cycles, weak-reference checks for released objects, queued-payload release while a native call is blocked, late-producer rejection, and ordered work on another window. They are simulated lifecycle tests; a packaged native-window smoke test is still needed. No frontend code changed, so a new frontend build was not needed for this source validation.

An isolated synthetic benchmark compared the old and changed functions with identical inputs using Python `tracemalloc`:

| Workload | Before | After |
|---|---:|---:|
| Peak tracked allocation while classifying a 32 MiB extensionless file | 33,624,601 bytes | 14,824 bytes |
| Payload retained by an idle worker after a 4 MiB script completed | 4,194,589 bytes | 148 bytes |
| Cached listings after 20 directory versions, 500 files each | 20 | 1 |

These are measurements of particular Python allocation paths, not a promise of a corresponding reduction in Task Manager or the entire app. The full script and raw measurements are in the local, git-ignored `docs/diagnostics/benchmark_memory_retention.py` and `docs/diagnostics/memory-retention-benchmark.json`. The benchmark used a temporary profile and synthetic files.

## What the running application currently uses

Read-only sampling at approximately 18:29 EDT found 13 Ducky-related processes, including one bridge parented by Cursor. That confirms the bridge was running; process presence alone is not an end-to-end tool-call test.

The final sample contained:

| Component | Private committed memory | Private resident memory |
|---|---:|---:|
| Verse language service | 407.6 MiB | 379.9 MiB |
| WebView renderer | 249.4 MiB | 216.7 MiB |
| Ducky application | 197.8 MiB | 173.2 MiB |
| WebView GPU process | 151.1 MiB | 64.3 MiB |
| One MCP bridge | 92.8 MiB | 89.2 MiB |
| Cloudflare tunnel | 65.8 MiB | 18.6 MiB |

The complete family, including other browser/console helpers, totaled approximately 1,250.7 MiB private commit and 999.8 MiB private resident memory. Three samples ranged from 1,247.7–1,250.7 MiB private commit. They are uncontrolled observations during normal use, not a before/after benchmark or proof of a leak. Private commit includes allocations that are not currently resident. The much smaller Task Manager screenshot sum does not include this complete set of components and uses a different display metric.

The Verse service provides code intelligence. Its memory should be counted when comparing equivalent editor workloads, not removed from the baseline by disabling diagnostics. The new fixes address demonstrated Python retention and allocation waste; they do not establish the cause of WebView renderer/GPU growth.

## Repeatable measurement and next checks

Use `scripts/measure_ducky_memory.ps1` to record read-only process-family JSON. It includes process identity, parent, role, host version where available, private committed bytes, private working set, total working set, threads and handles. It exports no full command lines or conversation contents. Missing private working-set counters are null, not zero. Process-tree membership is a fallback and can miss detached/reparented children; snapshots are not atomic.

Example, from the repository root:

```powershell
./scripts/measure_ducky_memory.ps1 -Samples 6 -IntervalSeconds 5 -Label 'visible-same-chat' > docs/diagnostics/visible-memory.json
```

For a fair release comparison, keep the same plugins, project, conversation, editor tabs, window size, bridge count and running language/tunnel services. Sample visible idle, repeated file/window operations, then hidden/minimized and restored. Run at least three matched trials. Include responsiveness and successful MCP calls, chat streaming, unsaved editor contents and tab handoff in the release smoke test.

The previous Low/Normal WebView policy remains applicable to inactive windows. Microsoft's [WebView2 performance guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/performance) recommends measuring realistic workloads and reducing inactive-view memory. The [memory-target API](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.memoryusagetargetlevel) is best effort and can move memory to disk; it does not guarantee lower active-page heap usage.

Remaining limits: an already-blocked native `evaluate_js` call cannot be safely interrupted by these changes; its queued work is released after destruction, but its in-flight frame remains until the call returns. Live-window queues still need a separate backpressure design with reliable event recovery. Further renderer reductions require a matched heap/profile investigation; indiscriminate history truncation, forced page reloads or disabled services would violate the functionality-preservation requirement.
