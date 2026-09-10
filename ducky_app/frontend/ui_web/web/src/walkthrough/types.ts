/** Product walkthrough step — targets use ui-targets registry ids. */

export type WalkthroughAdvance = "next" | "require_click";

export type WalkthroughSpotlightMode = "circle" | "rect";

export interface WalkthroughStep {
  /** Semantic ui-target id (e.g. `header.settings`, `settings.tab.store`). */
  target: string;
  title: string;
  body: string;
  advance: WalkthroughAdvance;
  mode?: WalkthroughSpotlightMode;
  /** Run before measuring / showing this step (open a tab, wait for mount). */
  onEnter?: () => void | Promise<void>;
}

export type WalkthroughAutoStart = "first_incomplete" | "never";

export interface WalkthroughDef {
  id: string;
  steps: WalkthroughStep[];
  /** When this tour completes, start this tour id once (if incomplete). */
  onCompleteStart?: string;
  autoStart?: WalkthroughAutoStart;
  title?: string;
  /** One-line blurb for Settings → Walkthrough list. */
  description?: string;
  /** When false, finish does not write walkthrough_completed (agent ephemeral tours). */
  persist?: boolean;
  /** Resolve steps at start (enabled plugin gateways, …). */
  resolveSteps?: () => WalkthroughStep[];
}

export interface WalkthroughRuntimeState {
  tourId: string | null;
  stepIndex: number;
  active: boolean;
}

/** Declarative plugin.json `contributes.walkthrough` row (no functions). */
export interface PluginWalkthroughManifest {
  id: string;
  title?: string;
  auto_start?: "first_enable" | "never";
  /** Settings sidebar tab to open before the first step. */
  settings_tab?: string;
  /** LLMs provider row id when it differs from plugin id (e.g. google → gemini). */
  provider_id?: string;
  steps: Array<{
    target: string;
    title: string;
    body: string;
    advance?: WalkthroughAdvance;
    mode?: WalkthroughSpotlightMode;
  }>;
  plugin_id?: string;
}
