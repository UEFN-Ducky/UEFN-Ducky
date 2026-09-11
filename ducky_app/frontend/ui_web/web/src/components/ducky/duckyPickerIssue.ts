/** Why a new ducky cannot run yet — never used to gray the picker tiles. */
export type DuckyPickerIssue = {
  message: string;
  actionLabel: string;
  actionTab: "LLMs" | "Store";
};

export type UsableModelAgent = {
  enabled?: boolean;
  available?: boolean;
  models?: unknown[];
};

/** Gateway catalog rows or any live coding-agent model list. */
export function hasUsableModels(opts: {
  modelsCount: number;
  agents?: UsableModelAgent[];
  /** Plugin stubs — used only while the live detect list has not landed. */
  contribCount?: number;
}): boolean {
  if (opts.modelsCount > 0) return true;
  const agents = opts.agents || [];
  if (
    agents.some(
      (a) => a.enabled !== false && a.available !== false && (a.models?.length ?? 0) > 0,
    )
  ) {
    return true;
  }
  if (agents.length === 0 && (opts.contribCount ?? 0) > 0) return true;
  return false;
}

export function duckyPickerIssue(opts: {
  gatewayCount: number;
  contribReady?: boolean;
  hasApiKey: boolean;
  catalogReady: boolean;
  modelsCount: number;
  agents?: UsableModelAgent[];
  /** Connected coding agents (Claude Code, Codex, …) bring their own models. */
  codingAgentCount?: number;
}): DuckyPickerIssue | null {
  if (opts.contribReady === false) return null;
  if (opts.gatewayCount <= 0) {
    return {
      message: "Install a gateway from Settings → Store → Gateways, then connect it in LLMs.",
      actionLabel: "Open Store",
      actionTab: "Store",
    };
  }
  if (!opts.hasApiKey) {
    return {
      message: "Connect your gateway in Settings → LLMs (add an API key). Duckies stay clickable.",
      actionLabel: "Open LLMs",
      actionTab: "LLMs",
    };
  }
  if (
    opts.catalogReady &&
    !hasUsableModels({
      modelsCount: opts.modelsCount,
      agents: opts.agents,
      contribCount: opts.codingAgentCount,
    })
  ) {
    return {
      message: "No models loaded yet. Test the API key in Settings → LLMs, or wait a moment.",
      actionLabel: "Open LLMs",
      actionTab: "LLMs",
    };
  }
  return null;
}
