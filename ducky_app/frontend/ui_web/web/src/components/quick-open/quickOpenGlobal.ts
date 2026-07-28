import type { QuickOpenMode } from "./quickOpenUtils";

export interface QuickOpenLaunchOptions {
  mode?: QuickOpenMode;
  query?: string;
}

type OpenFn = (opts: QuickOpenLaunchOptions) => void;

let openQuickOpen: OpenFn | null = null;

export function registerQuickOpenOpener(fn: OpenFn | null): void {
  openQuickOpen = fn;
}

export function openQuickOpenFromEditor(opts: QuickOpenLaunchOptions): void {
  openQuickOpen?.(opts);
}
