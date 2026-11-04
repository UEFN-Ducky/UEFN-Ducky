import { useEffect, useSyncExternalStore } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { onApiReady } from "./onApiReady";
import { getApi } from "./usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";
import { clearStoreCatalogCache, fetchStoreCatalog } from "./storeCatalogCache";

/** Shared Store-catalog update badge (Header + Settings + StoreTab). */

const POLL_MS = 600_000; // 10 minutes — not a hot loop; badge is best-effort

type Snapshot = { updateCount: number };

let snapshot: Snapshot = { updateCount: 0 };
const listeners = new Set<() => void>();
let inFlight = false;
let pollStarted = false;

function emit(): void {
  for (const fn of [...listeners]) fn();
}

function setUpdateCount(n: number): void {
  if (n === snapshot.updateCount) return;
  snapshot = { updateCount: n };
  emit();
}

function countUpdates(items: Array<{ state?: string }> | undefined): number {
  if (!items?.length) return 0;
  let n = 0;
  for (const item of items) {
    if (item.state === "update") n += 1;
  }
  return n;
}

/** Push catalog items from an already-fetched response (avoids a second round-trip). */
export function notifyStoreCatalogItems(items: Array<{ state?: string }> | undefined): void {
  setUpdateCount(countUpdates(items));
}

async function fetchUpdateCount(): Promise<void> {
  const api = getApi();
  if (inFlight || typeof api?.duckyos_store_catalog !== "function") return;
  inFlight = true;
  try {
    // Shared with the Setup wizard and Settings: one read at boot, then only what changed.
    const next = await fetchStoreCatalog(api);
    if (next && next.ok !== false) setUpdateCount(countUpdates(next.items));
  } catch {
    /* keep last known count */
  } finally {
    inFlight = false;
  }
}

export function refreshStoreUpdateBadge(): void {
  void fetchUpdateCount();
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
  return { updateCount: 0 };
}

function ensurePolling(): void {
  if (pollStarted) return;
  pollStarted = true;
  // Header is always mounted, so this hears account switches even when the Store is closed.
  installPanelPushBus();
  subscribePanelPush((event) => {
    if (event.type !== "duckyos_account_changed") return;
    clearStoreCatalogCache();
    void fetchUpdateCount();
  });
  onApiReady(() => {
    void fetchUpdateCount();
    setVisibleInterval(() => void fetchUpdateCount(), POLL_MS);
  });
}

/** Module-level cache + one poller shared by Header, SettingsView, StoreTab. */
export function useStoreUpdateBadge(): {
  updateCount: number;
  hasUpdates: boolean;
  refresh: () => void;
} {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    ensurePolling();
  }, []);

  return {
    updateCount: snap.updateCount,
    hasUpdates: snap.updateCount > 0,
    refresh: refreshStoreUpdateBadge,
  };
}
