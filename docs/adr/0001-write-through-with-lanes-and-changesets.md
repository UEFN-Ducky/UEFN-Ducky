# ADR 0001: Write-through project writes with lanes, a changeset journal, and attribution

Status: Accepted (2026-09-07)

## Context

UEFN-Ducky's agents ("duckies") edit the user's UEFN project on disk. Group
members run concurrently. Before this decision there was no shared write path:
at least seven independent code paths wrote project files, none coordinated,
and file-history attribution was a single `source: "agent"` flag. External
orchestrators that assign each worker a `write_allowed` lane and attribute the
final diff per worker rejected parallel direct writes on that basis.

A staging model (write to a shadow tree, review, then apply) was evaluated and
rejected because of how UEFN compiles Verse:

- `compileProject` on the Verse Workflow Server (`127.0.0.1:1962`) takes no
  arguments and compiles whatever project UEFN has open. There is no
  compile-a-string, compile-a-snippet, or temp-project path.
- The offline `verse-lsp` scan is rooted at the live project directory.
- Verse module identity derives from the folder path, so staging inside
  `Content/Verse/<staging>/` changes module paths and collides with live files.

Any write that is not on disk in the live project cannot be compile-verified,
which breaks the mandatory write → lint → compile loop.

## Decision

1. **Writes stay live (write-through).** Every project mutation lands on disk
   immediately so compile, LSP, and listener behaviour are unchanged.
2. **One pipeline.** All project writes go through `backend.workspace.writer.
   ProjectWriter`: resolve → identity → policy → per-path lock → read → atomic
   write → journal → observers. An architecture fitness test fails CI when a
   module writes a project file any other way.
3. **Identity is a `ContextVar`** (`RunContext`) bound per run and exported to
   external coding-agent processes as `DUCKY_*` environment variables.
4. **A changeset journal** records every applied write per run under AppData
   with content-addressed blobs, supports revert-file / revert-run, and flags
   (never denies) conflicts between concurrent runs.
5. **Lanes** are `write_allowed` glob lists stored on the group roster and
   enforced as a `WritePolicy`. Enforcement ships in `shadow` mode first and is
   flipped to `enforce` by a settings default in a later release.
6. **Glob semantics are a public contract**: gitignore-style, `**` spans
   directories, `*` and `?` never cross `/`, a bare directory means `dir/**`,
   case-insensitive on Windows.
7. **Dependency direction.** `backend/workspace` imports nothing from
   `frontend`. Frontend concerns register adapters through protocols
   (`WriteObserver`, `WritePolicy`, `LaneProvider`, event sinks) at startup.

## Alternatives considered

- **Stage inside `Content/Verse/.ducky_staging/<run>/`.** Compiles, but module
  paths change and duplicate definitions collide. Rejected.
- **True shadow directory outside the project.** Every reader would need an
  overlay and the tree still has to be copied in before compile. Rejected.
- **Per-call-site hooks.** Reproduces the original defect for the next write
  path. Rejected in favour of the single pipeline.
- **Lane policy file at `<project>/.ducky/lanes.json`.** Sits inside the agent
  write boundary, so a member could widen its own lane, and the project must
  not gain side-files. Rejected in favour of the roster.

## Consequences

- Attribution, revert, and lanes work for embedded duckies and for writes made
  through Ducky's own MCP tools from external CLI processes.
- Native `Write`/`Edit` calls made by external CLIs cannot be blocked (no
  pre-tool hook); they are observed, journaled with `in_lane=false`, and
  surfaced. Documented limitation.
- Machine state (journal, history) lives in AppData keyed by project slug.
- Schemas under `backend/workspace/schemas/` are versioned contracts; Python
  and TypeScript validate against the same fixtures.
