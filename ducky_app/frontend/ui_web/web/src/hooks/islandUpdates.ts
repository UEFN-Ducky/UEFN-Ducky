/**
 * Another PC pushed to the island UEFN has open (`island_updates`, from the app's island watch):
 * one row per island in the header's activity list. Ducky never pulls by itself; the row says how.
 * The Windows notification and the account's bell come from the backend's alert.
 */
import { upsertBackgroundJob } from "./backgroundActivity";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

let stop: (() => void) | null = null;

/** Idempotent: the app calls it once. */
export function installIslandUpdates(): void {
  if (stop) return;
  installPanelPushBus();
  stop = subscribePanelPush((event) => {
    if (event.type !== "island_updates" || !event.island) return;
    upsertBackgroundJob({
      id: `island-updates:${event.island}`,
      source: "revision-control",
      title: event.name || "Island",
      detail: event.behind
        ? `${event.detail || "New changes on the island"}. Ask a Ducky to pull them (revision_pull); it never pulls over unsaved work.`
        : event.detail || "Up to date.",
      phase: "done",
    });
  });
}

/** Tests: stop hearing, so the next install starts afresh. */
export function _resetIslandUpdatesForTests(): void {
  stop?.();
  stop = null;
}
