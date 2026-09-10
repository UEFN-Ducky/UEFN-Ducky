# Architecture Decision Records

One file per decision, numbered, never rewritten once accepted. A superseded
decision gets a new record that links back; the old one stays with its status
changed. Format: Context, Decision, Alternatives considered, Consequences.

- [0001](0001-write-through-with-lanes-and-changesets.md) — write-through project
  writes with lanes, a changeset journal, and attribution.
- [0002](0002-editor-change-tracking-and-revert.md) — editor change tracking,
  blocked-attempt recording, and revert. Extends 0001.
- [0003](0003-one-local-sqlite-database.md) — one local SQLite database
  (`ducky.db`, WAL) for all app state; direct multi-process access; per-store
  shadow migration. Reference: `docs/architecture/store.md`.
