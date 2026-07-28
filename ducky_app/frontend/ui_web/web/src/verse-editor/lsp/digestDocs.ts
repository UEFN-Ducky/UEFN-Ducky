/**
 * Digest-sourced completion docs.
 *
 * verse-lsp never ships `documentation`/`detail` on completion items and has no
 * `completionItem/resolve` (probed 2026-07-05: resolveProvider=false, items carry
 * label+insertText only). The doc comments the user expects live in the Verse /
 * Fortnite / UnrealEngine digest files inside the Saved/VerseProject workspace
 * folders, so we parse those once per session and enrich items client-side.
 */

import { readVerseFile } from "../api/verseEditorApi";
import { verseLspLog, verseLspWarn } from "./verseLspDebug";

export type DigestDocEntry = {
  /** Innermost enclosing class/module in the digest, e.g. "creative_prop". */
  owner: string;
  /** Full digest declaration line, e.g. "Show<native><public>()<transacts>:void". */
  signature: string;
  /** The `#` doc comment block right above the declaration. */
  doc: string;
};

type DigestIndex = Map<string, DigestDocEntry[]>;

const indexCache = new Map<string, Promise<DigestIndex>>();

const MAX_DIGEST_BYTES = 20_000_000;

function parseDigest(text: string, index: DigestIndex): number {
  // Scope stack of enclosing class/module/interface/enum declarations by indent.
  const scopeStack: Array<{ indent: number; name: string }> = [];
  let comments: string[] = [];
  let added = 0;

  for (const rawLine of text.split(/\r?\n/)) {
    const stripped = rawLine.trimStart();
    if (!stripped) {
      comments = [];
      continue;
    }
    // Attribute lines (`@available {...}`) sit between the doc comment and the
    // declaration — skip without dropping the accumulated comment.
    if (stripped.startsWith("@")) continue;

    const indent = rawLine.length - stripped.length;
    if (stripped.startsWith("#")) {
      comments.push(stripped.replace(/^#\s?/, ""));
      continue;
    }

    while (scopeStack.length && indent <= scopeStack[scopeStack.length - 1].indent) {
      scopeStack.pop();
    }

    const nameMatch = /^(?:var\s+)?([A-Za-z_][A-Za-z0-9_]*)/.exec(stripped);
    if (nameMatch) {
      const name = nameMatch[1];
      if (comments.length) {
        const owner = scopeStack.length ? scopeStack[scopeStack.length - 1].name : "";
        const list = index.get(name) ?? [];
        list.push({ owner, signature: stripped.replace(/\s+$/, ""), doc: comments.join("\n\n") });
        index.set(name, list);
        added++;
      }
      if (/:=\s*(?:class|interface|module|enum)\b/.test(stripped)) {
        scopeStack.push({ indent, name });
      }
    }
    comments = [];
  }
  return added;
}

async function buildIndex(folders: readonly string[]): Promise<DigestIndex> {
  const index: DigestIndex = new Map();
  await Promise.all(
    folders.map(async (folder) => {
      const normalized = folder.replace(/\\/g, "/").replace(/\/+$/, "");
      const base = normalized.split("/").pop() ?? "";
      if (!base) return;
      // "<Folder>/<Folder>.digest.verse", except the assets folder where
      // "MCPTest-Assets" holds "Assets.digest.verse" — try the "-" suffix too.
      const names = [...new Set([base, base.split("-").pop() ?? ""])].filter(Boolean);
      for (const name of names) {
        const digestPath = `abs:${normalized}/${name}.digest.verse`;
        try {
          // Optional probe: most workspace folders (Content, vproject) have no digest, and
          // the Assets folder's digest is "Assets.digest.verse" not "<Project>-Assets…", so
          // the first guess misses. Read quietly — these expected misses shouldn't log as errors.
          const { content } = await readVerseFile(digestPath, { quiet: true });
          if (content.length > MAX_DIGEST_BYTES) {
            verseLspWarn("digest-docs", "digest too large — skipped", { digestPath });
            return;
          }
          const added = parseDigest(content, index);
          verseLspLog("digest-docs", "digest parsed", { digestPath, entries: added });
          return;
        } catch {
          // Most workspace folders (Content, vproject) have no digest — expected.
        }
      }
    }),
  );
  verseLspLog("digest-docs", "index ready", { names: index.size });
  return index;
}

function ensureIndex(folders: readonly string[]): Promise<DigestIndex> {
  const key = folders.map((f) => f.toLowerCase()).sort().join(";");
  let promise = indexCache.get(key);
  if (!promise) {
    promise = buildIndex(folders).catch((err) => {
      indexCache.delete(key);
      throw err;
    });
    indexCache.set(key, promise);
  }
  return promise;
}

/** Kick off digest parsing in the background so the first lookup is instant. */
export function warmDigestDocs(folders: readonly string[]): void {
  if (!folders.length) return;
  void ensureIndex(folders).catch(() => {});
}

/**
 * Best digest entry for a completion label like "SetMaterial(Material:material, Index:int)".
 * When several classes declare the same member name, rank by identifier-token overlap
 * between the completion label and each digest signature.
 */
export async function lookupDigestDoc(
  folders: readonly string[],
  label: string,
): Promise<DigestDocEntry | null> {
  if (!folders.length) return null;
  const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(label)?.[0];
  if (!name) return null;
  let index: DigestIndex;
  try {
    index = await ensureIndex(folders);
  } catch {
    return null;
  }
  const candidates = index.get(name);
  if (!candidates?.length) return null;
  if (candidates.length === 1) return candidates[0];

  // Rank: matching call-shape ("Show()" is a function, not the `var Show` field;
  // "IsValid[]" is failable) dominates, then identifier-token overlap with the
  // signature (matches parameter names/types for overloads).
  const labelIsCall = label.includes("(");
  const labelIsFailable = label.includes("[");
  const labelTokens = new Set(label.toLowerCase().split(/[^a-z0-9_]+/).filter(Boolean));
  const shapeRe = (open: string) =>
    new RegExp(`^(?:var\\s+)?${name}(?:<[^>]*>)*\\s*\\${open}`);
  let best = candidates[0];
  let bestScore = -1;
  let bestCount = 0;
  for (const c of candidates) {
    let score = 0;
    if (labelIsCall === shapeRe("(").test(c.signature)) score += 10;
    if (labelIsFailable && shapeRe("[").test(c.signature)) score += 10;
    for (const t of new Set(c.signature.toLowerCase().split(/[^a-z0-9_]+/).filter(Boolean))) {
      if (labelTokens.has(t)) score++;
    }
    if (score > bestScore) {
      bestScore = score;
      best = c;
      bestCount = 1;
    } else if (score === bestScore) {
      bestCount++;
    }
  }
  // Several classes tie (Show() exists on ~20 devices with near-identical docs):
  // the doc text is still right, but don't assert a specific owner.
  return bestCount > 1 ? { ...best, owner: "" } : best;
}
