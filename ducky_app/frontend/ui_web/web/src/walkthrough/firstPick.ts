/**
 * `contributes.walkthrough.auto_start: "first_pick"`: a gateway's tour runs the first time the
 * person picks one of its models in a chat (Ducky Auto shows its rules and its sharing toggle).
 * Once finished or skipped it is completed, like every tour, and never starts by itself again.
 */
import { isCompleted, startTour } from "./WalkthroughService";
import { inferLlmsWalkthroughProviderId, pluginTourId } from "./pluginWalkthroughs";
import type { PluginWalkthroughManifest } from "./types";

export const MODEL_PICKED_EVENT = "ducky:model-picked";

const norm = (id: string) => (id || "").trim().toLowerCase().replace(/-/g, "_");

/** The first-pick tour for a gateway the person just picked, if it has one not yet done. */
export function firstPickTourFor(rows: PluginWalkthroughManifest[], providerKey: string): string {
  const pk = norm(providerKey);
  if (!pk) return "";
  for (const row of rows) {
    if (row.auto_start !== "first_pick") continue;
    if (norm(inferLlmsWalkthroughProviderId(row)) !== pk) continue;
    const id = pluginTourId(row.id);
    return isCompleted(id) ? "" : id;
  }
  return "";
}

export function installFirstPickTours(getRows: () => PluginWalkthroughManifest[]): () => void {
  const onPick = (ev: Event) => {
    const providerKey = String((ev as CustomEvent<{ providerKey?: string }>).detail?.providerKey || "");
    const tourId = firstPickTourFor(getRows(), providerKey);
    // After the picker closes, so the tour isn't drawn under it.
    if (tourId) window.setTimeout(() => void startTour(tourId), 350);
  };
  window.addEventListener(MODEL_PICKED_EVENT, onPick);
  return () => window.removeEventListener(MODEL_PICKED_EVENT, onPick);
}
