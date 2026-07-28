/** Open a provider usage editor tab (or focus if already open). */

import { getApi } from "../hooks/usePanelApi";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import { usageTabId } from "../types/panel";

export interface OpenUsageRequest {
  providerId: string;
  label?: string;
}

let openUsageTabFn: ((req: OpenUsageRequest) => void) | null = null;
const focusIfOpenFns = new Set<(tabId: string) => boolean>();

export function registerOpenUsageTab(fn: (req: OpenUsageRequest) => void): () => void {
  openUsageTabFn = fn;
  return () => {
    if (openUsageTabFn === fn) openUsageTabFn = null;
  };
}

/** Register a local "activate if this window already has the tab" probe. */
export function registerUsageTabFocusIfOpen(fn: (tabId: string) => boolean): () => void {
  focusIfOpenFns.add(fn);
  return () => {
    focusIfOpenFns.delete(fn);
  };
}

export function requestOpenUsageTab(req: OpenUsageRequest): void {
  const providerId = (req.providerId || "").trim();
  if (!providerId) return;
  openUsageTabFn?.({ ...req, providerId });
}

/**
 * Chart button: focus existing usage tab (this window or another) if open;
 * otherwise run `onSlide` to show the settings slide.
 */
export async function requestUsageSlideOrFocus(
  req: OpenUsageRequest,
  onSlide: () => void,
): Promise<void> {
  const providerId = (req.providerId || "").trim();
  if (!providerId) return;
  const id = usageTabId(providerId);
  const api = getApi();
  if (api?.focus_tab) {
    try {
      const res = await api.focus_tab(id, WINDOW_ID);
      if (res?.ok) return;
    } catch {
      /* open locally / slide */
    }
  }
  for (const fn of focusIfOpenFns) {
    if (fn(id)) return;
  }
  onSlide();
}
