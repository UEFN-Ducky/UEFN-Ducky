/**
 * Find what a tour or the AI points at, and get it on screen.
 *
 * A target is one of:
 * - a registry id (`workflows.toolbar.run`), tagged with `useUiTarget` / `targetRef`;
 * - a dynamic id a view resolves (`workflows.pin.<node>.in.<pin>`, `files.item.<path>`);
 * - a role + accessible name (`{role: "button", name: "Save", within: "workflows.details"}`),
 *   so anything with a label can be shown without new code.
 *
 * Views register resolvers for their id prefixes (find + reveal) and named UI actions that
 * get the screen ready first (`workflows.open`, `workflows.select`, …).
 */
import { getTargetElement, listTargets } from "./registry";

export interface TargetQuery {
  id?: string;
  role?: string;
  name?: string;
  text?: string;
  /** Look only inside this target (an id or another query). */
  within?: TargetSpec;
  /** Which match when several fit (0 = first visible). */
  nth?: number;
}

export type TargetSpec = string | TargetQuery;

export interface TargetResolver {
  find?: (id: string) => HTMLElement | null;
  /** Bring it into sight: open its folder, glide the canvas, scroll. */
  reveal?: (id: string) => void | Promise<void>;
}

const resolvers = new Map<string, TargetResolver>();

/** Handle every id starting with `prefix` (longest prefix wins). */
export function registerTargetResolver(prefix: string, resolver: TargetResolver): () => void {
  resolvers.set(prefix, resolver);
  return () => {
    if (resolvers.get(prefix) === resolver) resolvers.delete(prefix);
  };
}

function resolverFor(id: string): TargetResolver | null {
  let best = "";
  for (const prefix of resolvers.keys()) {
    if (id.startsWith(prefix) && prefix.length > best.length) best = prefix;
  }
  return best ? resolvers.get(best) ?? null : null;
}

const INTERACTIVE = "button, a[href], input, textarea, select, summary, [role], [tabindex], [contenteditable='true'], [aria-label], [title]";

/** The ARIA role of an element: explicit, else the one its tag implies. */
export function roleOf(el: Element): string {
  const explicit = (el.getAttribute("role") || "").trim().split(/\s+/)[0];
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (tag === "button" || tag === "summary") return "button";
  if (tag === "a" && el.hasAttribute("href")) return "link";
  if (tag === "textarea") return "textbox";
  if (tag === "select") return "combobox";
  if (tag === "input") {
    const type = ((el as HTMLInputElement).type || "text").toLowerCase();
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radio";
    if (type === "range") return "slider";
    if (type === "button" || type === "submit" || type === "reset") return "button";
    return "textbox";
  }
  if (/^h[1-6]$/.test(tag)) return "heading";
  if (tag === "img") return "img";
  if (tag === "li") return "listitem";
  if (tag === "aside") return "complementary";
  if (tag === "nav") return "navigation";
  if ((el as HTMLElement).isContentEditable) return "textbox";
  return "";
}

const squash = (text: string) => text.replace(/\s+/g, " ").trim();

/** CSS.escape, with a fallback where it's missing (tests). */
export function cssEscape(value: string): string {
  const css = (globalThis as { CSS?: { escape?: (v: string) => string } }).CSS;
  return css?.escape ? css.escape(value) : value.replace(/["\\\]\[]/g, "\\$&");
}

/** What a screen reader would call it: aria-label, labelledby, label, title, alt, text. */
export function accessibleName(el: Element): string {
  const aria = el.getAttribute("aria-label");
  if (aria && aria.trim()) return squash(aria);
  const labelledby = el.getAttribute("aria-labelledby");
  if (labelledby) {
    const text = labelledby.split(/\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" ");
    if (text.trim()) return squash(text);
  }
  const id = el.getAttribute("id");
  if (id) {
    const label = document.querySelector(`label[for="${cssEscape(id)}"]`);
    if (label?.textContent?.trim()) return squash(label.textContent);
  }
  const text = squash(el.textContent || "");
  if (text) return text;
  const title = el.getAttribute("title") || el.getAttribute("alt") || el.getAttribute("placeholder") || "";
  return squash(title);
}

export function isShown(el: Element): boolean {
  if (el.closest("[inert]")) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const style = window.getComputedStyle(el);
  return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
}

function pick(found: HTMLElement[], nth = 0): HTMLElement | null {
  const visible = found.filter(isShown);
  const pool = visible.length ? visible : found;
  return pool[Math.max(0, nth)] ?? null;
}

/** Best match by role and accessible name: exact, then starts with, then contains. */
export function findByRole(role: string, name: string, scope: ParentNode = document.body, nth = 0): HTMLElement | null {
  const want = squash(name).toLowerCase();
  const wantRole = role.trim().toLowerCase();
  const candidates = Array.from(scope.querySelectorAll<HTMLElement>(INTERACTIVE)).filter((el) => {
    if (el.closest(".showme-overlay, .walkthrough-overlay")) return false;
    return !wantRole || roleOf(el) === wantRole;
  });
  if (!want) return pick(candidates, nth);
  const named = candidates.map((el) => ({ el, name: accessibleName(el).toLowerCase() }));
  for (const test of [(n: string) => n === want, (n: string) => n.startsWith(want), (n: string) => n.includes(want)]) {
    const hits = named.filter((row) => test(row.name)).map((row) => row.el);
    if (hits.length) return pick(hits, nth);
  }
  return null;
}

/** The smallest shown element whose own text matches. */
export function findByText(text: string, scope: ParentNode = document.body, nth = 0): HTMLElement | null {
  const want = squash(text).toLowerCase();
  if (!want) return null;
  const hits = Array.from(scope.querySelectorAll<HTMLElement>("body *")).filter((el) => {
    if (el.closest(".showme-overlay, .walkthrough-overlay")) return false;
    if (!squash(el.textContent || "").toLowerCase().includes(want)) return false;
    return !Array.from(el.children).some((child) => squash(child.textContent || "").toLowerCase().includes(want));
  });
  return pick(hits, nth);
}

function asQuery(spec: TargetSpec): TargetQuery {
  return typeof spec === "string" ? { id: spec } : spec || {};
}

export function resolveTarget(spec: TargetSpec): HTMLElement | null {
  const q = asQuery(spec);
  const id = (q.id || "").trim();
  if (id) {
    const el = getTargetElement(id);
    if (el && el.isConnected && isShown(el)) return el;
    // Registered but out of sight (a layer slid away): the view may show it elsewhere.
    const other = resolverFor(id)?.find?.(id) ?? null;
    if (other) return other;
    return el && el.isConnected ? el : null;
  }
  const scope = q.within ? resolveTarget(q.within) : document.body;
  if (!scope) return null;
  if (q.role || q.name) return findByRole(q.role || "", q.name || "", scope, q.nth || 0);
  if (q.text) return findByText(q.text, scope, q.nth || 0);
  return null;
}

/** A short stable name for a target: its id, or `role "name"`. */
export function targetKey(spec: TargetSpec): string {
  const q = asQuery(spec);
  if (q.id) return q.id;
  const inside = q.within ? ` in ${targetKey(q.within)}` : "";
  if (q.role || q.name) return `${q.role || "any"} "${q.name || ""}"${inside}`;
  return q.text ? `text "${q.text}"${inside}` : "";
}

/** True when the spec names something (an id, a role/name, or text). */
export function isTargetSpec(raw: unknown): raw is TargetSpec {
  if (typeof raw === "string") return !!raw.trim();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  const q = raw as TargetQuery;
  return !!(q.id || q.role || q.name || q.text);
}

/** Bring a target into sight: the view's own reveal, else the canvas hook, else scroll. */
export async function revealTarget(spec: TargetSpec): Promise<void> {
  const q = asQuery(spec);
  const id = (q.id || "").trim();
  if (id) {
    const resolver = resolverFor(id);
    if (resolver?.reveal) {
      try {
        await resolver.reveal(id);
      } catch {
        /* still show it where it is */
      }
      return;
    }
    // A view can bring its own targets into sight (the Workflows canvas glides to a node).
    const reveal = new CustomEvent("ducky:ui-target-reveal", { detail: { id }, cancelable: true });
    if (!window.dispatchEvent(reveal)) return;
  }
  const el = resolveTarget(spec);
  try {
    el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  } catch {
    /* ignore */
  }
}

/** Wait until the target is on the page and shown (a view may still be opening). */
export async function waitForTarget(spec: TargetSpec, timeoutMs = 5000): Promise<HTMLElement | null> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const el = resolveTarget(spec);
    if (el && isShown(el)) return el;
    if (Date.now() >= end) return el;
    await new Promise((r) => globalThis.setTimeout(r, 100));
  }
}

// --------------------------------------------------------------------------- UI actions

export interface UiActionInfo {
  id: string;
  label: string;
  route: string;
}

type UiActionFn = (args: Record<string, unknown>) => void | Promise<void>;

const actions = new Map<string, { run: UiActionFn; label: string; route: string }>();

/** A named step that gets the screen ready (`workflows.open {id}`), for tours and Show me. */
export function registerUiAction(id: string, run: UiActionFn, meta: { label?: string; route?: string } = {}): () => void {
  const entry = { run, label: meta.label || id, route: meta.route || id.split(".")[0] };
  actions.set(id, entry);
  return () => {
    if (actions.get(id) === entry) actions.delete(id);
  };
}

/** Wait for a view to register an action (it mounts when its tab opens). */
export async function waitForUiAction(id: string, timeoutMs = 4000): Promise<boolean> {
  const end = Date.now() + timeoutMs;
  while (!actions.has(id) && Date.now() < end) await new Promise((r) => globalThis.setTimeout(r, 100));
  return actions.has(id);
}

export async function runUiAction(id: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; error?: string }> {
  const entry = actions.get(id);
  if (!entry) return { ok: false, error: `unknown action: ${id}` };
  try {
    await entry.run(args);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

export function listUiActions(route = ""): UiActionInfo[] {
  const hint = route.trim();
  return [...actions.entries()]
    .filter(([id, a]) => !hint || a.route.startsWith(hint) || id.startsWith(hint))
    .map(([id, a]) => ({ id, label: a.label, route: a.route }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** Registered targets matching a label/id search, shown ones first. */
export function searchTargets(route = "", query = "", visibleOnly = false) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = listTargets(route).filter((row) => {
    if (visibleOnly && !row.visible) return false;
    const hay = `${row.id} ${row.label}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  return rows.sort((a, b) => Number(b.visible) - Number(a.visible) || a.id.localeCompare(b.id));
}
