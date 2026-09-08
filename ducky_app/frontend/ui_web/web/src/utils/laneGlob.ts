/**
 * Write-lane globs — TypeScript port of `backend/workspace/lanes.py`.
 *
 * Same public contract (ADR 0001): `**` spans directories, `*` and `?` never
 * cross `/`, a bare directory means `dir/**`, matching is case-insensitive.
 * Both implementations run `backend/workspace/schemas/fixtures/lane_glob_cases.json`,
 * so the lane editor's instant feedback never disagrees with the server, which
 * stays authoritative.
 */

const WILDCARDS = new Set(["*", "?", "["]);

export class LaneGlobError extends Error {}

function hasWildcard(segment: string): boolean {
  for (const ch of segment) if (WILDCARDS.has(ch)) return true;
  return false;
}

export function normalizeGlob(pattern: string): string {
  let p = (pattern || "").trim().replace(/\\/g, "/");
  while (p.startsWith("./")) p = p.slice(2);
  if (!p) throw new LaneGlobError("empty lane pattern");
  if (p.startsWith("/") || (p.length > 1 && p[1] === ":")) {
    throw new LaneGlobError(`lane pattern must be project-relative: ${pattern}`);
  }
  if (p.split("/").some((seg) => seg === "..")) {
    throw new LaneGlobError(`lane pattern must not contain '..': ${pattern}`);
  }
  p = p.replace(/\/+$/, "");
  while (p.includes("//")) p = p.replace("//", "/");
  const last = p.split("/").pop() ?? "";
  const looksLikeDir = !hasWildcard(p) && !last.includes(".");
  if (looksLikeDir) p = `${p}/**`;
  return p;
}

/** `null` stays null (unrestricted); text splits on newlines/commas; dedupe, sort. */
export function normalizeLane(patterns: string[] | string | null | undefined): string[] | null {
  if (patterns == null) return null;
  const raw = typeof patterns === "string" ? patterns.split(/[\n,]/) : patterns;
  const out: string[] = [];
  for (const item of raw) {
    if (!item.trim()) continue;
    const norm = normalizeGlob(item);
    if (!out.includes(norm)) out.push(norm);
  }
  return out.sort();
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function translate(pattern: string): string {
  const segments = pattern.split("/");
  return segments
    .map((seg, i) => {
      const last = i === segments.length - 1;
      if (seg === "**") return last ? ".*" : "(?:[^/]+/)*";
      let buf = "";
      for (const ch of seg) {
        if (ch === "*") buf += "[^/]*";
        else if (ch === "?") buf += "[^/]";
        else buf += escapeRegex(ch);
      }
      return buf + (last ? "" : "/");
    })
    .join("");
}

const compiled = new Map<string, RegExp>();

export function compileGlob(pattern: string): RegExp {
  const norm = normalizeGlob(pattern);
  let re = compiled.get(norm);
  if (!re) {
    let body = translate(norm);
    if (norm.endsWith("/**")) {
      const head = translate(norm.slice(0, -"/**".length));
      body = `(?:${body})|(?:${head})`;
    }
    re = new RegExp(`^(?:${body})$`, "i");
    compiled.set(norm, re);
  }
  return re;
}

export function normalizeRel(path: string): string {
  let p = (path || "").trim().replace(/\\/g, "/");
  while (p.startsWith("./")) p = p.slice(2);
  return p.replace(/^\/+/, "");
}

export function match(pattern: string, relPath: string): boolean {
  return compileGlob(pattern).test(normalizeRel(relPath));
}

export function anyMatch(patterns: string[], relPath: string): boolean {
  return patterns.some((p) => match(p, relPath));
}

export function literalPrefix(pattern: string): string {
  const kept: string[] = [];
  for (const seg of normalizeGlob(pattern).split("/")) {
    if (hasWildcard(seg)) break;
    kept.push(seg);
  }
  return kept.join("/");
}

export function isDirGlob(pattern: string): boolean {
  return normalizeGlob(pattern).endsWith("/**");
}

export function isExactFile(pattern: string): boolean {
  return !hasWildcard(normalizeGlob(pattern));
}

function hasMidWildcard(pattern: string): boolean {
  const segments = normalizeGlob(pattern).split("/");
  const inner = segments.slice(0, -1);
  return inner.some((seg) => seg !== "**" && hasWildcard(seg)) || inner.includes("**");
}

function isPrefix(a: string, b: string): boolean {
  const la = a.toLowerCase();
  const lb = b.toLowerCase();
  return la === lb || (la !== "" && lb.startsWith(`${la}/`)) || la === "";
}

export type OverlapKind = "identical" | "contains" | "matches" | "maybe" | null;

export function overlap(a: string, b: string): OverlapKind {
  const na = normalizeGlob(a);
  const nb = normalizeGlob(b);
  if (na.toLowerCase() === nb.toLowerCase()) return "identical";
  const pa = literalPrefix(na);
  const pb = literalPrefix(nb);
  const related = isPrefix(pa, pb) || isPrefix(pb, pa);
  const plainA = isDirGlob(na) && !hasMidWildcard(na);
  const plainB = isDirGlob(nb) && !hasMidWildcard(nb);
  if (plainA && plainB && related) return "contains";
  if (plainA && isExactFile(nb) && isPrefix(pa, pb)) return "contains";
  if (plainB && isExactFile(na) && isPrefix(pb, pa)) return "contains";
  if (isExactFile(na) && match(nb, na)) return "matches";
  if (isExactFile(nb) && match(na, nb)) return "matches";
  if ((hasMidWildcard(na) || hasMidWildcard(nb)) && related) return "maybe";
  return null;
}

export interface LaneSetVerdict {
  errors: string[];
  warnings: string[];
}

/** Pairwise overlap across the members of one group (name → lane). */
export function checkLaneSet(lanes: Record<string, string[] | null | undefined>): LaneSetVerdict {
  const errors: string[] = [];
  const warnings: string[] = [];
  const items = Object.entries(lanes).filter(([, lane]) => lane && lane.length > 0) as [string, string[]][];
  for (let i = 0; i < items.length; i += 1) {
    const [ma, la] = items[i];
    for (let j = i + 1; j < items.length; j += 1) {
      const [mb, lb] = items[j];
      for (const ga of la) {
        for (const gb of lb) {
          const kind = overlap(ga, gb);
          if (!kind) continue;
          const text = `${ma}: ${ga} overlaps ${mb}: ${gb} (${kind})`;
          (kind === "maybe" ? warnings : errors).push(text);
        }
      }
    }
  }
  return { errors, warnings };
}

/** Lane editor text (one glob per line, commas allowed) → raw lines. */
export function parseLaneText(text: string): string[] {
  return (text || "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Per-line problems for the lane editor; empty when every line is a valid pattern. */
export function validateLaneGlobs(lines: string[]): string[] {
  const problems: string[] = [];
  for (const line of lines) {
    try {
      normalizeGlob(line);
    } catch (err) {
      problems.push(err instanceof Error ? err.message : String(err));
    }
  }
  return problems;
}

/** Chip label: "No lane", "Read-only", or the first glob with a +N suffix. */
export function shortLaneLabel(lane: string[] | null | undefined): string {
  if (lane == null) return "No lane";
  if (lane.length === 0) return "Read-only";
  const first = lane[0].replace(/\/\*\*$/, "/").replace(/^Content\//i, "");
  return lane.length > 1 ? `${first} +${lane.length - 1}` : first;
}
