import { useSyncExternalStore } from "react";
import type { CardBusy } from "../views/settings/store/storeData";

/**
 * In-flight Store install/update jobs — survives Settings tab switches.
 * StoreTab unmounts on leave; bridge downloads keep running, so UI state
 * must live outside the component. sessionStorage also survives a WebView
 * reload mid–Update All so Pending cards come back.
 */

type Snapshot = {
  jobs: Record<string, CardBusy>;
  hiddenToasts: Record<string, true>;
};

const STORAGE_KEY = "uefn-store-install-jobs";

function readPersistedJobs(): Record<string, CardBusy> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as { jobs?: Record<string, CardBusy> };
    const jobs = parsed?.jobs;
    if (!jobs || typeof jobs !== "object") return {};
    // Only restore in-flight — done/error flash after reload is noise.
    const out: Record<string, CardBusy> = {};
    for (const [slug, job] of Object.entries(jobs)) {
      if (job?.phase === "working" && slug) {
        out[slug] = { ...job, queued: true };
      }
    }
    return out;
  } catch {
    return {};
  }
}

function writePersistedJobs(jobs: Record<string, CardBusy>): void {
  try {
    const working: Record<string, CardBusy> = {};
    for (const [slug, job] of Object.entries(jobs)) {
      if (job.phase === "working") working[slug] = job;
    }
    if (Object.keys(working).length === 0) {
      sessionStorage.removeItem(STORAGE_KEY);
      return;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobs: working }));
  } catch {
    /* quota / private mode */
  }
}

let snapshot: Snapshot = { jobs: readPersistedJobs(), hiddenToasts: {} };
const listeners = new Set<() => void>();
/** Sync guard so double-clicks / remounts can't start a second download. */
const installingSlugs = new Set<string>();
const clearTimers = new Map<string, ReturnType<typeof setTimeout>>();
/**
 * One Store download/install at a time.
 * Update All used to fire N concurrent bridge jobs + N full plugin reloads,
 * which froze the WebView UI even though work kept finishing in the background.
 */
let installChain: Promise<void> = Promise.resolve();
/** Jobs queued or running through runStoreInstallExclusive. */
let exclusivePending = 0;
/** Catalog refresh after Update All — one refresh when the queue drains. */
let catalogDirty = false;
let onQueueIdle: (() => void | Promise<void>) | null = null;

function emit(): void {
  snapshot = { jobs: snapshot.jobs, hiddenToasts: snapshot.hiddenToasts };
  writePersistedJobs(snapshot.jobs);
  for (const fn of [...listeners]) fn();
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getSnapshot(): Snapshot {
  return snapshot;
}

function getServerSnapshot(): Snapshot {
  return { jobs: {}, hiddenToasts: {} };
}

export function useStoreInstallJobs(): Snapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** Working (queued + downloading) jobs — for Store tab badges. */
export function countWorkingStoreJobs(jobs: Record<string, CardBusy>): number {
  let n = 0;
  for (const job of Object.values(jobs)) {
    if (job.phase === "working") n += 1;
  }
  return n;
}

/** Sidebar label: 1…8, then 8+. */
export function formatStoreJobBadge(count: number): string {
  if (count <= 0) return "";
  return count > 8 ? "8+" : String(count);
}

/**
 * Live runner claimed this slug (beginStoreInstall).
 * Persisted "working" overlays without a runner are NOT busy — StoreTab
 * reclaims them after a reload so Update All can resume one-by-one.
 */
export function isStoreInstallBusy(slug: string): boolean {
  if (!slug) return false;
  return installingSlugs.has(slug);
}

/** True while any exclusive install is queued or running. */
export function isStoreInstallQueueActive(): boolean {
  return exclusivePending > 0;
}

/** Claim the slug for a new download. Returns false if already in flight. */
export function beginStoreInstall(slug: string): boolean {
  if (!slug || installingSlugs.has(slug)) return false;
  installingSlugs.add(slug);
  return true;
}

export function endStoreInstall(slug: string): void {
  installingSlugs.delete(slug);
}

/** Called by StoreTab to refresh catalog once when the install queue is empty. */
export function setStoreInstallQueueIdleHandler(
  fn: (() => void | Promise<void>) | null,
): void {
  onQueueIdle = fn;
}

/** Mark that installed/versions changed; idle handler refreshes once. */
export function markStoreCatalogDirty(): void {
  catalogDirty = true;
}

export function isStoreCatalogDirty(): boolean {
  return catalogDirty;
}

/** Run Store download/install exclusively (FIFO). Failures do not break the queue. */
export function runStoreInstallExclusive<T>(fn: () => Promise<T>): Promise<T> {
  exclusivePending++;
  const result = installChain.then(async () => {
    try {
      return await fn();
    } finally {
      exclusivePending--;
      if (exclusivePending === 0 && onQueueIdle) {
        try {
          await onQueueIdle();
        } catch {
          /* StoreTab refresh — ignore */
        }
      }
    }
  });
  installChain = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

export function patchStoreJob(slug: string, next: CardBusy | null): void {
  if (!slug) return;
  const jobs = snapshot.jobs;
  const hidden = snapshot.hiddenToasts;
  if (!next) {
    const t = clearTimers.get(slug);
    if (t) {
      clearTimeout(t);
      clearTimers.delete(slug);
    }
    if (!(slug in jobs) && !(slug in hidden)) return;
    const { [slug]: _j, ...restJobs } = jobs;
    const { [slug]: _h, ...restHidden } = hidden;
    snapshot = { jobs: restJobs, hiddenToasts: restHidden };
    emit();
    return;
  }
  snapshot = { jobs: { ...jobs, [slug]: next }, hiddenToasts: hidden };
  emit();
}

export function revealStoreJobToast(slug: string): void {
  if (!slug || !snapshot.hiddenToasts[slug]) return;
  const { [slug]: _drop, ...rest } = snapshot.hiddenToasts;
  snapshot = { jobs: snapshot.jobs, hiddenToasts: rest };
  emit();
}

/** Re-show every toast (e.g. returning to Store after Close / tab switch). */
export function revealAllStoreJobToasts(): void {
  if (Object.keys(snapshot.hiddenToasts).length === 0) return;
  snapshot = { jobs: snapshot.jobs, hiddenToasts: {} };
  emit();
}

export function hideStoreJobToast(slug: string): void {
  if (!slug || snapshot.hiddenToasts[slug]) return;
  snapshot = {
    jobs: snapshot.jobs,
    hiddenToasts: { ...snapshot.hiddenToasts, [slug]: true },
  };
  emit();
}

/** How long a finished toast stays before leaving the Updates stack. */
const DONE_TOAST_MS = 2200;

/**
 * Mark job done/error, flash it briefly, then remove it from the Updates list.
 * Working jobs stay; Close / Close all still dismiss immediately.
 */
export function clearStoreJobLater(slug: string, done: CardBusy): void {
  revealStoreJobToast(slug);
  const prev = clearTimers.get(slug);
  if (prev) {
    clearTimeout(prev);
    clearTimers.delete(slug);
  }
  patchStoreJob(slug, done);
  const t = setTimeout(() => {
    clearTimers.delete(slug);
    // Only drop if still terminal — a restarted install must not get wiped.
    const cur = snapshot.jobs[slug];
    if (cur && (cur.phase === "done" || cur.phase === "error")) {
      patchStoreJob(slug, null);
    }
  }, DONE_TOAST_MS);
  clearTimers.set(slug, t);
}

/** Hide working toasts; drop finished/error jobs. Card overlays keep in-flight progress. */
export function dismissAllStoreJobToasts(): void {
  for (const t of clearTimers.values()) clearTimeout(t);
  clearTimers.clear();
  const nextJobs: Record<string, CardBusy> = {};
  const nextHidden: Record<string, true> = {};
  for (const [slug, job] of Object.entries(snapshot.jobs)) {
    if (job.phase === "working") {
      nextJobs[slug] = job;
      nextHidden[slug] = true;
    }
  }
  snapshot = { jobs: nextJobs, hiddenToasts: nextHidden };
  emit();
}

/** Consume dirty flag (StoreTab idle refresh). */
export function takeStoreCatalogDirty(): boolean {
  if (!catalogDirty) return false;
  catalogDirty = false;
  return true;
}

/**
 * After WebView reload, working jobs are restored but have no live runner.
 * Invoke resume for each orphan so StoreTab re-enters the exclusive queue.
 */
export function reclaimOrphanStoreJobs(
  resume: (slug: string, job: CardBusy) => void,
): void {
  for (const [slug, job] of Object.entries(snapshot.jobs)) {
    if (job.phase !== "working" || installingSlugs.has(slug)) continue;
    resume(slug, job);
  }
}

/** ponytail: test-only peek; production never calls this. */
export function _peekStoreJobForTests(slug: string): CardBusy | undefined {
  return snapshot.jobs[slug];
}

/** ponytail: test-only reset; production never calls this. */
export function _resetStoreInstallJobsForTests(): void {
  for (const t of clearTimers.values()) clearTimeout(t);
  clearTimers.clear();
  installingSlugs.clear();
  installChain = Promise.resolve();
  exclusivePending = 0;
  catalogDirty = false;
  onQueueIdle = null;
  snapshot = { jobs: {}, hiddenToasts: {} };
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  emit();
}
