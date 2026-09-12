# Store migration test plan

Companion to [docs/architecture/store.md](../architecture/store.md). Long form:
artifact "ducky.db Test Plan" (https://claude.ai/code/artifact/79d02098-d609-478c-ae54-5839aa19bb92).

## Baseline (2026-09-10, `LOCALAPPDATA` isolated to an empty dir)

| suite | result |
|---|---|
| pytest `ducky_app tests` | 1213 pass, 3 fail regardless of environment, 12 pass only against a developer's real AppData, 2 skip |
| vitest `ui_web/web` | 738 pass, 3 fail (all `components/changes`) |
| dead | 6 `test_*.py` files collect zero tests (`main()` self-checks) |
| CI | `security.yml` gitleaks + pip-audit on PRs and daily |

Real failures: `test_mcp_inject::test_bootstrap_includes_chat_report_template`
(prompt text drift), `test_settings_schema::test_every_field_has_meta`
(`follow_code_off_migrated` has no `FIELD_META`), `test_no_direct_writes` (the
scanner matches `writer.write_text` in `journal._replay_run`, which *is* the
pipeline). Environment-dependent: `skills/test_lazy_index` ×3, `tools/uefn/
test_validate_uefn_asset_tool` ×2, `tools/verse/test_wire_preflight` ×1,
`tools/world/test_blockout_areas_tools` ×2, `tools/world/test_worldgen_tools` ×3,
`ui_web/test_close_changeset_run` ×1 (need Store plugins `uefn`, `verse`,
`leveldesign` enabled and seeded packs).

## Rules

1. A store moves only when its public-behaviour tests pass against both
   backends (`store_backend` fixture parametrised `files` / `db`).
2. Every store gets five tests: parity, import, shadow-diff, rollback,
   retention.
3. Contracts stay JSON: schemas under `backend/workspace/schemas/` validate the
   projections; React suites import the same fixtures.
4. Hermetic by construction: session fixture isolates `LOCALAPPDATA`; a guard
   fails the run if the real AppData changes.
5. Acceptance numbers are assertions with 3× ceilings.

## Step 0 — make the baseline true

- Fix the three real failures and the three vitest Changes-view failures.
- Make the twelve environment-dependent tests hermetic (seed packs in the
  isolated AppData; a fixture that enables gated plugin ids).
- Add the isolation fixture and the real-AppData guard to `conftest.py`.
- Convert the six `main()` self-check files to collected tests.
- Fix raw `os.environ["LOCALAPPDATA"]` mutations in
  `backend/uefn_plugins/test_store_sync.py`.
- Run both suites in CI, blocking.

## Layers

| L | proves | where |
|---|---|---|
| 1 | store core: pragmas, per-thread connections, migration ladder, FTS/JSON asserted, UNC refused, integrity + snapshot restore | `backend/store/test_db.py`, `test_migrations.py` |
| 2 | repo parity on both backends | existing public-behaviour tests + `store_backend` |
| 3 | importers on golden fixtures: counts, hashes, projection equality, idempotent, resumable | `backend/store/importers/test_*.py` |
| 4 | shadow diff after every operation | `backend/store/test_shadow.py` |
| 5 | concurrency + durability (real subprocesses) | `backend/store/test_concurrency.py`, `test_durability.py` |
| 6 | RPC contract: frozen `PanelApi` method list + DTO fixtures | `ui_web/test_panel_api_methods.py`, `test_panel_api_dtos.py` |
| 7 | 103 `ducky_*` MCP tools from a bridge with no panel | `backend/tools/panel/test_*.py` |
| 8 | React on regenerated DTO fixtures | `ui_web/web/src/**/*.test.ts(x)` |
| 9 | listener boundary and capture parity | `uefn_listener/listener/test_*.py`, `test_capture_parity.py` |
| 10 | end-to-end on the frozen EXE, empty machine and real-AppData copy | `scripts/e2e/` |
| 11 | performance budgets | `backend/store/test_perf_budgets.py`, `web/perf-chat` |

## Existing tests by fate

Keep (public behaviour, run on both backends): all of
`backend/workspace/` except the disk lines in `test_journal.py`; chats
(`test_save_conversation_keeps_messages`, `test_checkpoint_coding_turn`,
`test_clear_upstream_session`, `test_continue_interrupted`,
`test_stop_then_continue`, `test_group_folder_hubs`, `test_group_orchestrator`,
`test_lanes_roster`, `test_member_prompt_lanes`, `test_external_agent_reload`,
`backend/agent/test_chat_title`, `test_a2a_broker`, `test_plugin_auto_opt_in`,
`frontend/test_skill_snapshot_refresh`); usage (`test_provider_usage_log`,
`test_token_usage`, `test_sidebar_context_tokens`, `test_context_tokens`);
settings (`test_settings_schema`, `backend/tools/panel/test_panel_tools`,
`test_favorite_models`, `test_starter_llm_gateways`, `test_secrets`);
plans (`coding_agents/test_plans`); memory (`test_context_memory`,
`test_prompt_cache_epoch`, `providers/test_cache_utils`); skills, plugins,
MCP, bridge, listener suites.

Rewrite (assert on disk layout → importer/contract tests):
`frontend/test_appdata_inventory.py`, `backend/workspace/test_journal.py`
(16 lines), `test_schemas.py` (keep as projection contract),
`ui_web/test_workspace_bootstrap.py` (runs/r1.json, catalog.json),
`verse_editor/test_file_history_agent.py`, `lsp/test_diagnostics_cache.py`,
`backend/memory/test_project.py`, `mcp_plugins/test_mcp_json_store.py`,
`ui_web/test_workspace_dock.py`, `frontend/test_error_log.py`,
`ui_web/test_models_cache_disk.py`, skills pack-layout tests,
`test_starter_llm_gateways.py`, `test_secrets.py`, `test_tool_captures.py`,
`test_plugin_host_api.py`, `test_uefn_plugins.py`, `test_no_direct_writes.py`
(allowlist the store package).

Untested today: `coding_agents/epic.py` (task store), `session_files.py`,
`conversation_attachments.py`, `window_bounds.py`, `editor_workspace.py`,
`recent_projects.py`, `tab_registry.py`, `editor_state_registry.py`,
`agent_profiles.py` storage, `ducky_assets.py`, `agent_crash_log.py`,
`perf_trace.py`, `atomic_json.py`, error_log size rotation, capture prune.

## Golden fixture (`tests/fixtures/appdata_golden/`)

Anonymised copy of a real AppData: the 560 KB chat with a 154 KB tool
message; a group hub with lanes; attachments; tri-state fields; a legacy
flat-layout chat; one `tmp*` leak slug; a ledger with editor entries,
inverses, a revert and its redo, blocked and failed rows, an opaque bracket,
a run-id sanitising collision, an orphan `Content_<hash>` ledger; file
history v1 + v2 and a renamed file; plans with legacy todos, a mangled
overview, `.bak` siblings, demo + user templates; memory flat, split and
session-notes; a 72-key settings file with patches; a legacy `mcp_plugins/`
folder; a test DPAPI `credentials.dat`; all six JSONL logs with a truncated
last line.

## Concurrency and durability

`test_two_processes_append_same_chat`, `test_reader_never_blocks_writer`,
`test_busy_timeout_then_error`, `test_kill_mid_transaction`,
`test_av_share_lock_retry`, `test_disk_full`,
`test_corrupt_db_restores_snapshot`, `test_snapshot_consistent_under_load`,
`test_unc_path_refused`, `test_newer_schema_refused`, `test_migration_ladder`,
`test_data_version_invalidation`.

## Budgets (3× ceilings)

append message 6 ms / 64 KB WAL; list 575 chats 30 ms, zero file opens;
settings loads per turn 1 per process; typing writes 0; journal record 1
transaction, 0 JSON files; `end_run` 0 files opened; watcher idle 0 bytes;
memory index 0 files; boot walk = blob and plugin dirs only.

## Gates

Gate 0 foundation → Gate 1 settings → Gate 2 conversations → Gate 3 ledger →
Gate 4 plans/memory/telemetry → Gate 5 cutover. Each: parity on both
backends, golden import exact and idempotent, shadow diff empty across the
suite and three golden boots, rollback passes, budgets pass, DTO fixtures
regenerated and vitest green.

## What exists on the branch (2026-09-10)

| test | covers |
|---|---|
| `backend/store/test_db.py` | connect/migrate, pragmas, capabilities, snapshots, integrity restore, trace |
| `backend/store/test_fitness.py` | only the store package imports sqlite3; the listener never imports the store |
| `backend/store/test_phase1.py` … `test_phase4.py` | each store's repo + importer against real legacy layouts |
| `backend/store/test_phase56.py` | mcp rows + export, captures, diagnostics rows, digest trigram index, paged loads, catalog cache, perf rows, skill manifest cache, `db` CLI, legacy retirement, extra logs |
| `backend/store/test_upgrade_boot.py` | first boot over a populated pre-database AppData (checked-in samples the old release wrote): every store imported, no stray files, `legacy/` gone after three clean boots |
| `frontend/test_store_admin.py` | Settings → App Data → Database backend: overview, masked previews, every action, staged restore, project row deletion |
| `web/src/views/settings/AppDataDatabase.test.tsx` | the Database tab renders health/tables/snapshots/leftovers and routes every button through `store_action` |
| `build/upgrade_proof/` | end-to-end: the old code generates a real AppData, the new code and the frozen EXE (`db import`, `db stats`) upgrade it |

Isolation: `pytest.ini` pins rootdir so the per-test AppData fixture always
loads, and `db.connect()` refuses the real `%LOCALAPPDATA%/UEFN-Ducky` under pytest.
