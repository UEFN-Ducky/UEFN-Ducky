/**
 * Host walkthrough registry + runner.
 *
 * Tours are owned by id (`app.shell`, `chat.composer`, `plugin.translation`, …).
 * Completion is persisted externally via setCompletedMap / markCompleted callbacks.
 */

import { usageTrack } from "../hooks/usageTrack";
import type { WalkthroughDef, WalkthroughRuntimeState, WalkthroughStep } from "./types";

/** Settings list order — Welcome, Chat, then each Settings tour. */
export const HOST_TOUR_CATALOG_IDS = [
  "app.shell",
  "chat.composer",
  "workflows.intro",
  "workflows.first_build",
  "workflows.editor",
  "settings.general",
  "settings.duckies",
  "settings.plans",
  "settings.llms",
  "settings.appearance",
  "settings.audio",
  "settings.store",
  "llms.setup",
] as const;

export const SETTINGS_CORE_SPLIT_IDS = [
  "settings.general",
  "settings.duckies",
  "settings.plans",
  "settings.llms",
  "settings.appearance",
  "settings.audio",
] as const;

type Listener = (state: WalkthroughRuntimeState) => void;
type FinishHook = (tourId: string, reason: "complete" | "skip") => void;

let finishHook: FinishHook | null = null;

/** Optional hook (agent ephemeral tours) — called after a tour ends. */
export function setWalkthroughFinishHook(fn: FinishHook | null): void {
  finishHook = fn;
}

const defs = new Map<string, WalkthroughDef>();
const listeners = new Set<Listener>();

let completed: Record<string, boolean> = {};
let persistCompleted: ((map: Record<string, boolean>) => void) | null = null;

let activeTourId: string | null = null;
let stepIndex = 0;
let entered = false;
let starting = false;
let activeResolvedSteps: WalkthroughStep[] = [];

function emit(): void {
  const state: WalkthroughRuntimeState = {
    tourId: activeTourId,
    stepIndex,
    active: activeTourId !== null,
  };
  for (const l of listeners) {
    try {
      l(state);
    } catch {
      /* ignore subscriber errors */
    }
  }
}

function writeCompleted(next: Record<string, boolean>): void {
  completed = next;
  persistCompleted?.(next);
}

export function setWalkthroughPersist(fn: ((map: Record<string, boolean>) => void) | null): void {
  persistCompleted = fn;
}

function migrateCompletedMap(map: Record<string, boolean>): Record<string, boolean> {
  const next = { ...map };
  if (next["settings.core"]) {
    for (const id of SETTINGS_CORE_SPLIT_IDS) next[id] = true;
  }
  return next;
}

/** Replace in-memory completion map (used once at hydrate from disk). */
export function setCompletedMap(map: Record<string, boolean>): void {
  completed = migrateCompletedMap(map);
}

export function getCompletedMap(): Record<string, boolean> {
  return { ...completed };
}

export function isCompleted(tourId: string): boolean {
  return !!completed[tourId];
}

/** Persist a tour as completed without starting/stopping the overlay. */
export function markTourCompleted(tourId: string): void {
  if (!tourId || completed[tourId]) return;
  writeCompleted({ ...completed, [tourId]: true });
}

export function registerTour(def: WalkthroughDef): void {
  if (!def.id || !Array.isArray(def.steps) || def.steps.length === 0) return;
  defs.set(def.id, def);
}

export function unregisterTour(tourId: string): void {
  defs.delete(tourId);
}

export function getTour(tourId: string): WalkthroughDef | undefined {
  return defs.get(tourId);
}

export function listTourIds(): string[] {
  return [...defs.keys()].sort();
}

export interface HostTourInfo {
  id: string;
  title: string;
  description: string;
}

/** Named host tours for Settings → Walkthrough (stable catalog order). */
export function listHostTours(): HostTourInfo[] {
  return HOST_TOUR_CATALOG_IDS.map((id) => {
    const def = defs.get(id);
    return {
      id,
      title: def?.title ?? id,
      description: def?.description ?? "",
    };
  });
}

export function subscribeWalkthrough(listener: Listener): () => void {
  listeners.add(listener);
  listener({
    tourId: activeTourId,
    stepIndex,
    active: activeTourId !== null,
  });
  return () => listeners.delete(listener);
}

export function getWalkthroughState(): WalkthroughRuntimeState {
  return { tourId: activeTourId, stepIndex, active: activeTourId !== null };
}

export function getActiveSteps(): WalkthroughStep[] {
  if (!activeTourId) return [];
  if (activeResolvedSteps.length) return activeResolvedSteps;
  return defs.get(activeTourId)?.steps ?? [];
}

export function getActiveStep(): WalkthroughStep | null {
  const steps = getActiveSteps();
  return steps[stepIndex] ?? null;
}

/** Whether the active step's onEnter has finished (its view is open). */
export function isStepEntered(): boolean {
  return entered;
}

async function enterStep(index: number): Promise<void> {
  entered = false;
  try {
    await runOnEnter(index);
  } finally {
    entered = true;
  }
}

async function runOnEnter(index: number): Promise<void> {
  const steps = getActiveSteps();
  const step = steps[index];
  if (!step) return;
  if (step.onEnter) {
    try {
      await step.onEnter();
    } catch {
      /* fail-soft — still show the step */
    }
  }
}

/** Start a tour (on step `at`, 0-based, to resume it). No-ops if already active on the same id, or def missing. */
export async function startTour(tourId: string, opts?: { force?: boolean; at?: number }): Promise<boolean> {
  const def = defs.get(tourId);
  if (!def) return false;
  if (!opts?.force && isCompleted(tourId)) return false;
  if (activeTourId === tourId) return true;
  if (activeTourId && !opts?.force) return false;
  if (starting) return false;
  starting = true;
  try {
    const resolved = def.resolveSteps?.() ?? def.steps;
    if (!resolved.length) return false;
    activeTourId = tourId;
    stepIndex = Math.min(Math.max(0, Math.floor(opts?.at ?? 0)), resolved.length - 1);
    entered = false;
    activeResolvedSteps = resolved;
    emit();
    await enterStep(stepIndex);
    emit();
    return true;
  } finally {
    starting = false;
  }
}

export async function nextStep(): Promise<void> {
  if (!activeTourId) return;
  const steps = getActiveSteps();
  if (stepIndex >= steps.length - 1) {
    await finishTour("complete");
    return;
  }
  stepIndex += 1;
  entered = false;
  emit();
  await enterStep(stepIndex);
  emit();
}

export async function prevStep(): Promise<void> {
  if (!activeTourId || stepIndex <= 0) return;
  stepIndex -= 1;
  entered = false;
  emit();
  await enterStep(stepIndex);
  emit();
}

const HOST_CHAIN = ["app.shell", "settings.store", "llms.setup"] as const;
const STARTER_PLUGIN_TOURS = ["plugin.anthropic", "plugin.openai", "plugin.google", "plugin.cursor"] as const;

async function finishTour(reason: "complete" | "skip"): Promise<void> {
  const id = activeTourId;
  const def = id ? defs.get(id) : undefined;
  const chain = def?.onCompleteStart;
  const persist = def?.persist !== false;
  // Anonymous count: which tour, finished or skipped, and on which step (1-based).
  if (id) usageTrack("tour.end", { tour: id, how: reason === "complete" ? "done" : "skipped", at: String(stepIndex + 1) });
  activeTourId = null;
  stepIndex = 0;
  activeResolvedSteps = [];
  emit();
  if (id && persist) {
    const next = { ...completed, [id]: true };
    // Skip = dismiss the whole first-run chain, not "advance to the next tour".
    if (reason === "skip" && (HOST_CHAIN as readonly string[]).includes(id)) {
      for (const hid of HOST_CHAIN) next[hid] = true;
      for (const pid of STARTER_PLUGIN_TOURS) next[pid] = true;
    }
    if (reason === "skip" && (STARTER_PLUGIN_TOURS as readonly string[]).includes(id)) {
      for (const pid of STARTER_PLUGIN_TOURS) next[pid] = true;
      next["llms.setup"] = true;
    }
    writeCompleted(next);
  }
  if (id) finishHook?.(id, reason);
  if (reason === "complete" && chain && !isCompleted(chain)) {
    // Small delay so Settings / Store can finish mounting after require_click.
    globalThis.setTimeout(() => {
      void startTour(chain, { force: true });
    }, 400);
  }
}

export async function skipTour(): Promise<void> {
  await finishTour("skip");
}

export async function completeTour(): Promise<void> {
  await finishTour("complete");
}

/** Clear completion for one tour and start it again. */
export async function redoTour(tourId: string): Promise<boolean> {
  const next = { ...completed };
  delete next[tourId];
  writeCompleted(next);
  if (activeTourId) {
    activeTourId = null;
    stepIndex = 0;
    activeResolvedSteps = [];
    emit();
  }
  return startTour(tourId, { force: true });
}

/** Clear host chain completions and restart `app.shell`. */
export async function redoAppWalkthrough(): Promise<boolean> {
  const next = { ...completed };
  for (const id of HOST_CHAIN) {
    delete next[id];
  }
  writeCompleted(next);
  if (activeTourId) {
    activeTourId = null;
    stepIndex = 0;
    activeResolvedSteps = [];
    emit();
  }
  return startTour("app.shell", { force: true });
}

/**
 * Start Welcome (`app.shell`) at step 1 when it has not been finished or skipped.
 * Gateway seeding does not decide whether the tour exists.
 * Returns false when the tour is already done or could not start (caller may retry).
 */
export async function startAppShellIfNeeded(): Promise<boolean> {
  if (isCompleted("app.shell")) return false;
  return startTour("app.shell");
}

/**
 * Auto-start the first registered tour with autoStart=first_incomplete that is not done.
 * Marks it completed immediately so force-quit / update cannot re-offer on every launch.
 *
 * Disabled 2026-07-20: WalkthroughOverlay rAF + setState hit React #185 and bricked
 * app open (1.0.565). First-run uses startAppShellIfNeeded instead.
 */
export function autoStartPending(): void {
  // ponytail: re-enable after WalkthroughOverlay is proven crash-free in production.
  return;
}

/** Test helper — reset in-memory state without touching persistence. */
export function _resetWalkthroughServiceForTests(): void {
  defs.clear();
  completed = {};
  persistCompleted = null;
  finishHook = null;
  activeTourId = null;
  stepIndex = 0;
  starting = false;
  activeResolvedSteps = [];
  emit();
}
