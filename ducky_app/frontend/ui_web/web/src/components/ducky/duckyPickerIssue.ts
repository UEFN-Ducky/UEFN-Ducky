/** Why a new ducky cannot run yet — never used to gray the picker tiles. */
export type DuckyPickerIssue = {
  message: string;
  actionLabel: string;
  actionTab: "LLMs" | "Store";
};

export function duckyPickerIssue(opts: {
  gatewayCount: number;
  contribReady?: boolean;
  hasApiKey: boolean;
  catalogReady: boolean;
  modelsCount: number;
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
  if (opts.catalogReady && opts.modelsCount <= 0) {
    return {
      message: "No models loaded yet. Test the API key in Settings → LLMs, or wait a moment.",
      actionLabel: "Open LLMs",
      actionTab: "LLMs",
    };
  }
  return null;
}
