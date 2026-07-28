/**
 * Registry of spotlightable panel controls.
 *
 * Components tag a DOM node with `useUiTarget("<semantic-id>")`; the guided-UI
 * MCP tools (ducky_ui_list_targets / ducky_walkthrough_run) look controls up by
 * that id. Ids are stable and semantic (`settings.mcp.apply`), never DOM
 * indexes, so a tool can reference a control without knowing the tree shape.
 */
import { useCallback, useRef } from "react";

export type UiTargetKind =
  | "tab"
  | "button"
  | "input"
  | "toggle"
  | "dropdown"
  | "chat"
  | "settings_field"
  | "plugin_row"
  | "skill_row";

export interface UiTargetMeta {
  label?: string;
  route?: string;
  kind?: UiTargetKind;
}

interface Entry {
  el: HTMLElement;
  meta: UiTargetMeta;
}

const registry = new Map<string, Entry>();

export function registerTarget(id: string, el: HTMLElement, meta: UiTargetMeta = {}): void {
  registry.set(id, { el, meta });
}

export function unregisterTarget(id: string): void {
  registry.delete(id);
}

export function getTargetElement(id: string): HTMLElement | null {
  return registry.get(id)?.el ?? null;
}

function isVisible(el: HTMLElement): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
  return true;
}

function isEnabled(el: HTMLElement): boolean {
  if ((el as HTMLButtonElement).disabled) return false;
  return el.getAttribute("aria-disabled") !== "true";
}

function labelFor(id: string, entry: Entry): string {
  if (entry.meta.label) return entry.meta.label;
  const text = (entry.el.getAttribute("aria-label") || entry.el.textContent || "").trim();
  if (text) return text.length > 60 ? `${text.slice(0, 57)}…` : text;
  return id;
}

export interface UiTargetInfo {
  id: string;
  label: string;
  route: string;
  rect: { x: number; y: number; w: number; h: number };
  visible: boolean;
  enabled: boolean;
  kind: UiTargetKind | "button";
}

/** Snapshot every registered target; `routeHint` filters by declared route prefix. */
export function listTargets(routeHint = ""): UiTargetInfo[] {
  const hint = routeHint.trim();
  const out: UiTargetInfo[] = [];
  for (const [id, entry] of registry) {
    const route = entry.meta.route ?? "";
    if (hint && route && !route.startsWith(hint) && !id.startsWith(hint)) continue;
    const rect = entry.el.getBoundingClientRect();
    out.push({
      id,
      label: labelFor(id, entry),
      route,
      rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
      visible: isVisible(entry.el),
      enabled: isEnabled(entry.el),
      kind: entry.meta.kind ?? "button",
    });
  }
  out.sort((a, b) => a.id.localeCompare(b.id));
  return out;
}

/**
 * Attach a stable spotlight id to a DOM node. Returns a ref callback:
 * `<button ref={useUiTarget("settings.mcp.apply", { kind: "button" })}>`.
 */
export function useUiTarget(id: string, meta: UiTargetMeta = {}) {
  const metaRef = useRef(meta);
  metaRef.current = meta;
  return useCallback(
    (el: HTMLElement | null) => {
      if (el) registerTarget(id, el, metaRef.current);
      else unregisterTarget(id);
    },
    [id],
  );
}

const refCache = new Map<string, (el: HTMLElement | null) => void>();
const metaCache = new Map<string, UiTargetMeta>();

/**
 * Non-hook variant of {@link useUiTarget} for use inside `.map()` where hooks
 * can't run. Returns a per-id stable ref callback (no re-register churn) and
 * refreshes the stored metadata on each render.
 */
export function targetRef(id: string, meta: UiTargetMeta = {}) {
  metaCache.set(id, meta);
  let fn = refCache.get(id);
  if (!fn) {
    fn = (el: HTMLElement | null) => {
      if (el) registerTarget(id, el, metaCache.get(id) ?? {});
      else unregisterTarget(id);
    };
    refCache.set(id, fn);
  }
  return fn;
}

/** Slug for a settings tab label, e.g. "Skills & MCP" → "skills-mcp". */
export function settingsTabTargetId(label: string): string {
  const slug = label
    .toLowerCase()
    .replace(/&/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .join("-");
  return `settings.tab.${slug}`;
}
