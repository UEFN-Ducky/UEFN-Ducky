/**
 * Standalone Verse source formatter — pure `string in → string out`, zero dependencies.
 *
 * This module is deliberately self-contained and framework-free so a human can read,
 * test, and extend it in isolation. Each formatting step is a small named function; the
 * pipeline lives in `formatVerseDocument`. To add a rule, write a `(lines) => lines`
 * (or `(string) => string`) function and slot it into that pipeline.
 *
 * ── Why relative-depth normalization (and not a token-based reindenter) ───────────────
 * Verse is whitespace-significant like Python: a block is introduced EITHER by braces
 * `{ }` OR by a header line ending in `:` / `=` followed by an indented suite that is
 * closed only by a decrease in indentation. A reindenter that recomputes depth purely
 * from tokens cannot know where a colon/`=` suite ends, so it cascades everything after
 * the first header rightward and corrupts real code.
 *
 * Instead we TRUST the relative indentation the author already wrote (it has to be
 * correct for the file to compile) and only normalize its WIDTH: every distinct step-in
 * becomes exactly one logical level rendered at `indentSize`. That safely fixes mixed
 * tabs/spaces, 2-vs-4-space inconsistency, and stray off-by-one indentation, for both
 * brace-style and colon-suite Verse, without ever moving code between logical levels.
 *
 * ── Scope of this version (documented so expectations are honest) ─────────────────────
 *   • Normalizes leading indentation to uniform levels.
 *   • Trims trailing whitespace, collapses long blank runs, guarantees one final newline.
 *   • Does NOT touch intra-line spacing/alignment, and leaves the interior of `<# #>`
 *     block comments byte-for-byte untouched (they often hold intentional alignment/art).
 */

export interface VerseFormatOptions {
  /** Spaces per indent level. Ignored when `indentChar` is a tab. */
  indentSize: number;
  /** Character used to build one indent level (" " or "\t"). */
  indentChar: " " | "\t";
  /** Runs of more than this many blank lines are collapsed down to this many. */
  maxConsecutiveBlankLines: number;
}

export const DEFAULT_VERSE_FORMAT_OPTIONS: VerseFormatOptions = {
  indentSize: 4,
  indentChar: " ",
  maxConsecutiveBlankLines: 1,
};

const BLOCK_COMMENT_OPEN = "<#";
const BLOCK_COMMENT_CLOSE = "#>";

/** Format an entire Verse document. Returns the input unchanged when it is only whitespace. */
export function formatVerseDocument(
  source: string,
  options: Partial<VerseFormatOptions> = {},
): string {
  if (source.trim() === "") return source;

  const opts: VerseFormatOptions = { ...DEFAULT_VERSE_FORMAT_OPTIONS, ...options };
  const eol = detectEol(source);
  const lines = source.split(/\r\n|\r|\n/);

  const reindented = reindentByRelativeDepth(lines, opts); // also trims trailing whitespace
  const collapsed = collapseBlankRuns(reindented, opts.maxConsecutiveBlankLines);
  const body = dropTrailingBlankLines(collapsed);

  return body.join(eol) + eol;
}

/** Preserve the document's dominant line ending so we don't rewrite every EOL. */
function detectEol(source: string): "\r\n" | "\n" {
  const firstBreak = source.indexOf("\n");
  return firstBreak > 0 && source[firstBreak - 1] === "\r" ? "\r\n" : "\n";
}

/** Visual column width of a line's leading whitespace, expanding tabs to `tabWidth`. */
function expandLeadingWhitespace(line: string, tabWidth: number): number {
  let width = 0;
  for (const ch of line) {
    if (ch === " ") width += 1;
    else if (ch === "\t") width += tabWidth - (width % tabWidth);
    else break;
  }
  return width;
}

/** True when a line opens a `<#` block comment that it does not also close on the same line. */
function opensUnterminatedBlockComment(content: string): boolean {
  const open = content.lastIndexOf(BLOCK_COMMENT_OPEN);
  if (open === -1) return false;
  return content.indexOf(BLOCK_COMMENT_CLOSE, open + BLOCK_COMMENT_OPEN.length) === -1;
}

/**
 * Re-emit every non-blank line at a uniform indent derived from the author's relative
 * indentation. `widths` is a stack of the source indent widths seen so far; its size
 * minus one is the current logical depth. Block-comment interiors pass through verbatim.
 */
function reindentByRelativeDepth(lines: string[], opts: VerseFormatOptions): string[] {
  const unit = opts.indentChar === "\t" ? "\t" : " ".repeat(Math.max(1, opts.indentSize));
  const tabWidth = Math.max(1, opts.indentSize);
  const out: string[] = [];
  const widths: number[] = [0];
  let inBlockComment = false;

  for (const raw of lines) {
    if (inBlockComment) {
      out.push(raw);
      if (raw.includes(BLOCK_COMMENT_CLOSE)) inBlockComment = false;
      continue;
    }

    const content = raw.trim();
    if (content === "") {
      out.push("");
      continue;
    }

    const width = expandLeadingWhitespace(raw, tabWidth);
    const top = () => widths[widths.length - 1];
    if (width > top()) {
      widths.push(width); // stepped in → one level deeper
    } else {
      while (widths.length > 1 && width < top()) widths.pop(); // stepped out → dedent
      if (width > top()) widths.push(width); // landed between known levels → treat as its own
    }

    out.push(unit.repeat(widths.length - 1) + content);

    if (opensUnterminatedBlockComment(content)) inBlockComment = true;
  }

  return out;
}

/** Collapse runs of more than `maxBlank` consecutive blank lines down to `maxBlank`. */
function collapseBlankRuns(lines: string[], maxBlank: number): string[] {
  const out: string[] = [];
  let blanks = 0;
  for (const line of lines) {
    if (line === "") {
      blanks += 1;
      if (blanks <= maxBlank) out.push("");
    } else {
      blanks = 0;
      out.push(line);
    }
  }
  return out;
}

function dropTrailingBlankLines(lines: string[]): string[] {
  const out = lines.slice();
  while (out.length > 0 && out[out.length - 1] === "") out.pop();
  return out;
}
