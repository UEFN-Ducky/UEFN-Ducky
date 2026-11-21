/** How a chat's turns are paid. Mirrors frontend/favorite_models.billing_kind. */
export type BillingKind = "subscription" | "api_key" | "local" | "account" | "";

const LOCAL_PROVIDERS = new Set(["ollama", "lmstudio", "llamacpp"]);

// How each plugin gateway says its turns are paid (its `billing` option), from the contributions.
let gatewayBilling = new Map<string, BillingKind>();

export function setGatewayBilling(rows: Array<{ id: string; billing?: string }>): void {
  gatewayBilling = new Map(
    rows
      .filter((r) => r.billing === "account" || r.billing === "subscription" || r.billing === "local" || r.billing === "api_key")
      .map((r) => [r.id.trim().toLowerCase(), r.billing as BillingKind]),
  );
}

export function billingKind(codingAgent: string | undefined, provider: string | undefined, isLocal = false): BillingKind {
  const agent = (codingAgent || "").trim().toLowerCase();
  if (agent && agent !== "ducky") return "subscription";
  const prov = (provider || "").trim().toLowerCase();
  if (!prov) return "";
  const declared = gatewayBilling.get(prov);
  if (declared) return declared;
  if (isLocal || LOCAL_PROVIDERS.has(prov)) return "local";
  return "api_key";
}

export const BILLING_TITLE: Record<Exclude<BillingKind, "">, string> = {
  subscription: "Runs on the coding agent's own login (Codex, Claude Code…): your subscription, not per-token billing.",
  api_key: "Billed per token to your saved API key. Agents may only start chats on it when you allow it in Settings → LLMs.",
  local: "Runs on a model on this PC: no per-token cost.",
  account: "Runs on your UEFN Ducky account.",
};
