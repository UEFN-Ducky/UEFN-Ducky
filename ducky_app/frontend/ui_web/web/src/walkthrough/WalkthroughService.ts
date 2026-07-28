/**
 * Host walkthrough registry + runner.
 *
 * Tours are owned by id (`app.shell`, `settings.core`, `plugin.translation`, …).
 * Completion is persisted externally via setCompletedMap / markCompleted callbacks.
 */

import type { WalkthroughDef, WalkthroughRuntimeState, WalkthroughStep } from "./types";

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
let starting = false;

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

/** Replace in-memory completion map (used once at hydrate from disk). */
export function setCompletedMap(map: Record<string, boolean>): void {
  completed = { ...map };
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
  return defs.get(activeTourId)?.steps ?? [];
}

export function getActiveStep(): WalkthroughStep | null {
  const steps = getActiveSteps();
  return steps[stepIndex] ?? null;
}

async function enterStep(index: number): Promise<void> {
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

/** Start a tour. No-ops if already active on the same id, or def missing. */
export async function startTour(tourId: string, opts?: { force?: boolean }): Promise<boolean> {
  const def = defs.get(tourId);
  if (!def) return false;
  if (!opts?.force && isCompleted(tourId)) return false;
  if (activeTourId === tourId && stepIndex === 0) return true;
  if (starting) return false;
  starting = true;
  try {
    activeTourId = tourId;
    stepIndex = 0;
    emit();
    await enterStep(0);
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
  emit();
  await enterStep(stepIndex);
  emit();
}

export async function prevStep(): Promise<void> {
  if (!activeTourId || stepIndex <= 0) return;
  stepIndex -= 1;
  emit();
  await enterStep(stepIndex);
  emit();
}

const HOST_CHAIN = ["app.shell", "settings.core", "settings.store"] as const;

async function finishTour(reason: "complete" | "skip"): Promise<void> {
  const id = activeTourId;
  const def = id ? defs.get(id) : undefined;
  const chain = def?.onCompleteStart;
  const persist = def?.persist !== false;
  activeTourId = null;
  stepIndex = 0;
  emit();
  if (id && persist) {
    const next = { ...completed, [id]: true };
    // Skip = dismiss the whole first-run chain, not "advance to the next tour".
    if (reason === "skip" && (HOST_CHAIN as readonly string[]).includes(id)) {
      for (const hid of HOST_CHAIN) next[hid] = true;
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
    emit();
  }
  return startTour(tourId, { force: true });
}

/** Clear host chain completions and restart `app.shell`. */
export async function redoAppWalkthrough(): Promise<boolean> {
  const next = { ...completed };
  for (const id of ["app.shell", "settings.core", "settings.store"]) {
    delete next[id];
  }
  writeCompleted(next);
  if (activeTourId) {
    activeTourId = null;
    stepIndex = 0;
    emit();
  }
  return startTour("app.shell", { force: true });
}

/**
 * Auto-start the first registered tour with autoStart=first_incomplete that is not done.
 * Marks it completed immediately so force-quit / update cannot re-offer on every launch.
 *
 * Disabled 2026-07-20: WalkthroughOverlay rAF + setState hit React #185 and bricked
 * app open (1.0.565). Manual replay via Settings → Add to UEFN still works.
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
  emit();
}
