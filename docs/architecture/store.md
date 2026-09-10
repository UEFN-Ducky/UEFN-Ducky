# The store: `ducky.db`

Reference for the state migration decided in [ADR 0003](../adr/0003-one-local-sqlite-database.md).
The long-form plan with the full inventory and measurements is the artifact
"ducky.db" (https://claude.ai/code/artifact/13205207-7e06-47b3-839d-91ea5d712c03);
this file is the version that lives with the code.

## Layout

```
backend/store/
  db.py            open(), per-thread connections, pragmas, migrate(),
                   integrity_check(), snapshot(), write_txn()
  migrations/      0001_init.sql … NNNN_*.sql, forward-only
  repos/           settings.py, chats.py, ledger.py, history.py, plans.py,
                   memory.py, usage.py, events.py, caches.py, plugins.py
  importers/       one per legacy store: read files → rows → verify
  export.py        JSON projections for contracts that stay
                   (ducky.changeset/1, file_history_entry, config.json)
  test_fitness.py  no sqlite3 outside store/, no AppData JSON writers

%LOCALAPPDATA%/UEFN-Ducky/
  ducky.db  ducky.db-wal  ducky.db-shm
  snapshots/ducky-YYYYMMDD-HHMMSS.db     (VACUUM INTO, keep 3)
  legacy/<store>/                        (renamed originals, deleted after 3 clean boots)
```

## Connection contract

| | |
|---|---|
| Open | one `sqlite3.Connection` per thread via `threading.local`, `isolation_level=None` |
| Pragmas | `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`, `temp_store=MEMORY`, `cache_size=-32768` |
| Writes | `BEGIN IMMEDIATE` … `COMMIT`; never across an HTTP, LLM or listener call, never while holding another subsystem's lock |
| Reads | plain `SELECT`; WAL readers never block writers |
| Processes | every Ducky Python process opens the file directly |
| Caches | in-memory snapshots revalidate with `PRAGMA data_version` |
| Blobs | text under 1 MB in `blobs`; larger text and all binary stays on disk with an index row |
| Refusals | UNC path, `user_version` newer than the app, failed integrity check without a snapshot |

## Schema (target)

| table | key columns | replaces |
|---|---|---|
| `projects` | id (slug), path, name, last_opened | recent_projects.json, five slug helpers |
| `settings` | key PK, value JSON, updated | panel_settings.json (one row per field; absence = default) |
| `secrets` | name PK, dpapi_blob | credentials.dat |
| `conversations` | id, project_id, folder_id, title, sort_order, updated, profile_id, model, coding_agent, is_group, leader_conv_id, parent_conv_id, file_path, tool_call_count, file_count, state JSON | conversation.json minus messages |
| `messages` | id, conv_id, seq, role, ts, run_id, text, blocks JSON, incomplete, error | conversation.json → messages[] |
| `message_fts` | FTS5(text, content=messages) | the title-only chat search |
| `snapshots` | hash PK, kind, text | skill_snapshot, prompt_cache_snapshot, context_summary |
| `folders`, `group_members`, `attachments` | | folders.json, roster, attachments/ |
| `usage_calls` | ts, conv_id, provider, model, agent, ducky, tokens, cost | provider_usage.jsonl + per-chat token_usage.calls |
| `runs`, `run_entries`, `run_seen` | run_id; (run_id, seq); (run_id, path) | changesets runs/*.json, index.json, catalog.json |
| `blobs` | hash PK, bytes, size | changesets blobs/*.txt + file_history content |
| `file_versions` | project_id, path, saved_at, hash → blobs, attribution | file_history/** |
| `watch_index` | (project_id, path), hash, mtime_ns, size | human_index.json |
| `plans`, `plan_nodes` | plan_id; (plan_id, node_id), parent_id, ordinal, status | .ducky/plans, plan_templates/ |
| `tasks`, `task_phases`, `task_artifacts` | | .ducky/tasks |
| `memory_entries`, `memory_fts` | (project_id, name), parent, description, author, body | memory/projects/**/*.md |
| `events` | ts, kind, source, level, message, payload | errors, activity, crashes, plugin load errors, verse error stats |
| `perf_events`, `perf_reports` | | perf/*.jsonl, latest-report.json |
| `verse_diagnostics` | (project_id, path), mtime_ns, size, items JSON | .ducky_verse_scan.json |
| `plugins`, `skill_manifest_cache`, `model_cache`, `cache_docs`, `plugin_kv`, `verse_templates`, `workspace_state`, `captures` | | the remaining small stores |

Tri-state list fields (`null` = inherit, `[]` = explicitly none) stay JSON and
are never flattened to `NOT NULL DEFAULT '[]'`.

## Encryption

Anything secret is stored the way API keys are today: a DPAPI blob per row
(`CryptProtectData`, entropy `UEFN-Ducky-v1-credentials`, user scope), never
plaintext, never logged, never exported. That covers:

- provider API keys and the DuckyOS session (`secrets`);
- plugin secrets declared in `plugin.json` and resolved through `${SECRET:NAME}`
  (`secrets`, keyed `plugin:<id>:<name>`);
- plugin key/value data a plugin marks `sensitive` (`plugin_kv.encrypted = 1`,
  value is the DPAPI blob); everything else in `plugin_kv` is plain JSON;
- Discord bot tokens and similar connector credentials.

The database file itself is not encrypted: it lives in the user's own profile
with the same ACLs as `credentials.dat` today, and whole-file encryption would
make snapshots, support dumps and the bridge processes depend on a key service.
Rows that must be secret are encrypted at the column, which is the model the
app already uses.

## Migration shape (every store)

1. Add the table and repo behind the existing function signatures.
2. Import on first open; verify counts, per-record projection equality, blob
   hashes; idempotent and resumable.
3. Shadow: legacy writer and repo both run; a diff hook compares the DB
   projection to the file after every operation.
4. Cut over reads; rename legacy files into `legacy/<store>/`.
5. Delete the legacy tree after three verified boots; delete the JSON code.

`settings.store_backend.<store> = files` flips one store back during its
shadow release.

## Phases

| phase | scope | exit |
|---|---|---|
| 0 | `backend/store/db.py`, `0001_init.sql`, freeze guard for `_sqlite3`, AppData registration and protection, test isolation fixture, fitness test, ADR | frozen EXE opens the DB on a clean machine; the suite writes nothing to the real AppData |
| 1 | settings, secrets, recent projects, window bounds, dock, editor workspace, models cache, listener manifest, plugin kv; `config.json` becomes a projection | one settings parse per process per boot; no lost update between panel and bridge |
| 2 | conversations, messages, folders, attachments index, usage, snapshots; FTS; big message bodies → blobs | sidebar under 10 ms; append under 2 ms; body search works; `backups/chats` stops growing |
| 3 | runs, entries, seen, blobs, file versions, watch index; revert lookups become queries; watcher stat-gated | ledger tests pass unchanged; export byte-equal; idle watcher reads 0 bytes |
| 4 | plans, tasks, templates, memory (+FTS), events, perf, diagnostics, skill manifest cache, plugin registry, captures | no `.ducky/` in a fresh project; memory index one query per turn |
| 5 | delete JSON writers, `atomic_json` backups, `_has_overrides`, `_iter_run_docs`; maintenance = integrity, snapshot, retention, blob GC, legacy delete; `db check|vacuum|snapshot|export` | grep finds JSON I/O only in importers, listener and IDE-config merge |
| 6 | optional: verse digest index, project file index, `mcp.json` → table, store catalog cache, paged `load_messages` | |

## What never goes in the database

Attachments and screenshots, audio, skill pack markdown, plugin trees, the
listener source, `verse-lsp/`, `webview2_browser/`, installer caches. Also the
advisory bootstrap files that run before the DB opens: `ship_stamp.json`,
`listener/.deploy_stamp`, `panel.pid`.

## Hot paths this removes (ranked)

1. Message append: read ×2, backup copy, serialise ×3, write, fsync, two backup-dir scans → one row.
2. Sidebar list parses every conversation with messages → one indexed query.
3. Typing writes `config.json` every 500 ms via `apply_workspace_env` → nothing.
4. Watcher reads every Verse file every 2 s → stat-gated.
5. One ledger write rewrites run, catalog, index; every read rewrites the run → one transaction.
6. `end_run` → `prune()` parses every run and lists every blob → maintenance query.
7. 203 settings parses → one per process, revalidated by `data_version`.
8. Memory index parses every `.md` per turn → one query.
9. Plan loaded and normalised before and after every mutating tool → two columns.
10. Revert and watcher dedupe parse every run doc → one query.
11. Provider usage read-all/rewrite-all per LLM call with a shared temp name → insert.
12. File history full-content per version, full read to compare a hash → blob refs.
13. Every process walks all of AppData twice at boot → queries.
14. Lane check reads two whole conversations on a cache miss → one query.
