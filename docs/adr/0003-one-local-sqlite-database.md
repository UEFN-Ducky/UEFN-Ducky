# ADR 0003: One local SQLite database for all app state

Status: Proposed (2026-09-10) — implementation on branch `feature/sqlite-store`.

Extends [ADR 0001](0001-write-through-with-lanes-and-changesets.md) (project
file writes) and [ADR 0002](0002-editor-change-tracking-and-revert.md) (editor
changes). This record covers where Ducky keeps its *own* state.

## Context

An inventory on 2026-09-10 found roughly 40 independent stores under
`%LOCALAPPDATA%/UEFN-Ducky/` and `<project>/.ducky/`: JSON documents, JSONL
logs, directory-per-record trees, content-addressed blobs and three hand-rolled
index files (`index.json`, `catalog.json`, `human_index.json`). They share four
different atomic-write implementations and no cross-process lock, although the
panel, one MCP bridge per IDE and one bridge per coding-agent run all write the
same files.

Measured costs on a developer machine:

- appending one message rewrites the whole conversation (560 KB) plus a byte
  copy into `backups/` plus an fsync, six to ten times per agent turn;
  `backups/` had grown to 886 MB from 26 MB of live chats;
- the sidebar parses every conversation including every message on every
  change event;
- `PanelSettings.load()` parses the settings file at 203 call sites with no
  cache;
- one agent file write rewrites three ledger documents; the end of every turn
  re-parses every run document in the project;
- the external-edit watcher reads every Verse file under the island every two
  seconds;
- typing in the composer writes `config.json` to disk every 500 ms.

Only five stores carry a schema version. Migrations are healing code on read
paths, several of which write to disk inside a read.

## Decision

1. **One database.** `%LOCALAPPDATA%/UEFN-Ducky/ducky.db` in WAL mode holds
   every store that is rows: settings, secrets, projects, conversations,
   messages, folders, usage, the changeset ledger and its blobs, file history,
   plans, tasks, memory, telemetry, caches, workspace state, plugin registry.
2. **One owner.** `backend/store/` is the only package that imports `sqlite3`.
   A fitness test fails the build otherwise, in the style of
   `backend/workspace/test_no_direct_writes.py`.
3. **Direct multi-process access.** Every Ducky Python process opens the file
   itself. A bridge must work with the panel closed, so there is no
   client/server hop. Concurrency is SQLite's own file locking with
   `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, one
   connection per thread, writers use `BEGIN IMMEDIATE`, and no transaction is
   held across an HTTP, LLM or listener call.
4. **What stays on disk.** Attachments, screenshots, audio, skill pack markdown
   (open Agent Skills format, deployed to IDEs as files), plugin trees, the
   listener source tree, the verse-lsp binary and the WebView2 profile. The DB
   indexes them.
5. **The listener boundary is unchanged.** The in-editor listener runs on
   Epic's Python 3.11, never imports the app and never opens the database. It
   keeps reading `config.json`, which becomes a projection the panel writes
   from the `settings` table, and its error append file is ingested by the
   panel.
6. **Schema versioning.** `PRAGMA user_version` is the schema version.
   Migrations are forward-only numbered SQL files under
   `backend/store/migrations/`, each applied in one transaction. A database
   written by a newer app is refused, never downgraded.
7. **Migration shape, per store.** Add table and repo behind the existing
   function signatures → import on first open with verification (counts,
   hashes, projection equality) → shadow release with both writers and a diff
   hook → cut reads over, rename legacy files to `legacy/` → delete after three
   verified boots. A `store_backend` override flips one store back to files.
8. **Backups become snapshots.** `VACUUM INTO snapshots/ducky-<ts>.db` before
   every migration and daily, keep three. No per-write byte copies.
9. **Corruption is loud.** `PRAGMA integrity_check` at boot; on failure the
   newest snapshot is restored and a banner event is recorded. No store may
   fall back to "all defaults" silently.
10. **Contracts stay JSON.** `ducky.changeset/1` export, `file_history_entry`,
    lane sets and `config.json` keep their schemas under
    `backend/workspace/schemas/` and validate the projections the DB emits.

## Alternatives considered

- **Keep files, add caches and an index.** Fixes reads, not the whole-file
  rewrites, the backup amplification or the cross-process races. Rejected.
- **The panel owns the DB; bridges go through loopback HTTP.** Makes the IDE
  integration depend on the GUI being open. Rejected.
- **A document store (JSON per row in SQLite, no columns).** Loses indexed
  lists and FTS, which are the point. Hybrid instead: columns for what is
  listed and searched, JSON for pass-through fields.
- **An external database server.** Wrong shape for a single-user desktop app
  and adds an install step. Rejected.

## Consequences

- The frozen EXE must ship `_sqlite3.pyd` and `sqlite3.dll`; the freeze guard
  in `build/unified.spec` enforces it. Cost about 1.5 MB.
- `appdata_maintenance` must know and protect `ducky.db`, `ducky.db-wal`,
  `ducky.db-shm` and `snapshots/` before any other phase lands.
- Tests get a session fixture that isolates `LOCALAPPDATA` and a guard that
  fails the run if the real AppData changes.
- The plan and its test plan live in `docs/architecture/store.md` and
  `docs/testing/store-test-plan.md`.
