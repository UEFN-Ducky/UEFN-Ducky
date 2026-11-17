/** Product walkthrough step — targets use ui-targets registry ids. */
import type { TargetSpec } from "../ui-targets/resolve";

export type WalkthroughAdvance = "next" | "require_click";

export type WalkthroughSpotlightMode = "circle" | "rect";

export interface WalkthroughStep {
  /** Semantic ui-target id (e.g. `header.settings`, `settings.tab.store`). */
  target: string;
  /** A role/name or text target when there's no id (`{role: "button", name: "Save"}`). */
  spec?: TargetSpec;
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
  /** The step's onEnter (open a view, select a node) has finished: safe to bring its target into view. */
  entered?: boolean;
}

/** Declarative plugin.json `contributes.walkthrough` row (no functions). */
export interface PluginWalkthroughManifest {
  id: string;
  title?: string;
  /** first_pick: the first time the person picks this gateway's model in a chat. */
  auto_start?: "first_enable" | "never" | "first_pick";
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
