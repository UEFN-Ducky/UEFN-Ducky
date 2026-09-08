# Architecture Decision Records

One file per decision, numbered, never rewritten once accepted. A superseded
decision gets a new record that links back; the old one stays with its status
changed. Format: Context, Decision, Alternatives considered, Consequences.

- [0001](0001-write-through-with-lanes-and-changesets.md) — write-through project
  writes with lanes, a changeset journal, and attribution.
- [0002](0002-editor-change-tracking-and-revert.md) — editor change tracking,
  blocked-attempt recording, and revert. Extends 0001.
