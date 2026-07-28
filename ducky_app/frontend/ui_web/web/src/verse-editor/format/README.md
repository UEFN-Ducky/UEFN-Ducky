# Verse formatter

Self-contained, client-side formatter for Verse source. Powers the editor's
**Format Document** action (right-click → Format Document, or Shift+Alt+F).

It does **not** use verse-lsp — Epic's language server ships no formatting
capability, so the old provider silently did nothing. This one runs entirely in the
browser and works even when the LSP is down.

## Files

| File | Responsibility |
| --- | --- |
| `verseFormatter.ts` | Pure `string in → string out` logic. No Monaco, no DOM. This is the file to edit when you want to change how code is formatted. |
| `registerVerseFormatter.ts` | Adapts `formatVerseDocument` to Monaco's `DocumentFormattingEditProvider`. Registered once from `setupMonaco`. |
| `index.ts` | Barrel exports. |

## What it does today

1. **Indentation normalization** — every distinct step-in becomes one logical level
   rendered at the editor's tab width (default 4 spaces). Fixes mixed tabs/spaces,
   2-vs-4-space inconsistency, and stray indentation.
2. **Trailing whitespace** — stripped from every line.
3. **Blank lines** — runs longer than one blank line are collapsed to one; the file
   ends with exactly one newline.

## Why it normalizes *relative* indentation instead of recomputing from tokens

Verse is whitespace-significant (like Python): a block is opened by braces `{ }` **or**
by a header ending in `:` / `=` whose suite is closed only by a decrease in indentation.
A token-based reindenter can't tell where a colon/`=` suite ends, so it cascades
everything rightward and breaks real code. Instead we trust the relative indentation the
author already wrote (it must be correct to compile) and only make its width uniform.

## Extending

Each step is a small named function in `verseFormatter.ts`. To add a rule, write a
`(lines: string[]) => string[]` (or `(s: string) => string`) function and slot it into
the pipeline in `formatVerseDocument`. Keep the module dependency-free so it stays easy
to unit-test in isolation.

Deliberately out of scope for now (add carefully if needed): intra-line spacing around
operators, alignment, and reformatting the interior of `<# #>` block comments (left
untouched today to preserve intentional alignment).
