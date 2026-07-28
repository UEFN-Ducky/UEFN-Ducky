# Archived: skill-pack-studio graph/node editor

Snapshot of the canvas-based node-graph editor (2026-07-03), taken before the
studio was replaced with the file-based editor (`src/skill-pack-studio/`).

The graph model (pack.json + subskills/ + layout coordinates + parent/child
edges) was replaced by the open Agent Skills layout: one `SKILL.md` plus flat
`references/*.md` files per pack, so packs work 1:1 in Claude Code and Cursor.

This folder is outside `tsconfig`'s `include` and is not compiled or bundled.
Safe to delete once the file-based studio has proven itself.
