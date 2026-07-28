/**
 * Group model rows by the vendor that makes the model, for the selector's
 * single-open accordions.
 *
 * Ducky catalog rows already carry a real vendor in `provider` (Anthropic,
 * OpenAI, …). Coding-agent rows (Cursor/Codex) report the *agent* as their
 * provider, so their vendor is derived from the model name instead — turning a
 * single flat agent list into tidy per-maker groups.
 */
import type { CatalogModelRow } from "../hooks/modelsCatalogCache";

export const VENDOR_ORDER = [
  "Anthropic",
  "OpenAI",
  "Google",
  "xAI",
  "Cursor",
  "Moonshot",
  "Zhipu",
  "Meta",
  "Mistral",
  "DeepSeek",
  "Qwen",
  "Ollama",
  "Other",
];

/** `provider` values that are coding-agent labels, not model makers. */
export const AGENT_LABEL_PROVIDERS = new Set([
  "Ducky",
  "Claude Code",
  "Codex",
  "Cursor",
  "Gemini CLI",
]);

/** Best-effort vendor from a model name/id (used for coding-agent model lists). */
export function vendorFromName(text: string): string {
  const s = (text || "").toLowerCase();
  if (/(opus|sonnet|haiku|fable|claude)/.test(s)) return "Anthropic";
  if (/(gpt|codex|davinci|turbo|o[134]\b)/.test(s)) return "OpenAI";
  if (/(gemini|gemma|palm|bison)/.test(s)) return "Google";
  if (/grok/.test(s)) return "xAI";
  if (/(composer|cursor|\bauto\b)/.test(s)) return "Cursor";
  if (/(kimi|moonshot)/.test(s)) return "Moonshot";
  if (/(glm|zhipu|chatglm)/.test(s)) return "Zhipu";
  if (/llama/.test(s)) return "Meta";
  if (/(mistral|mixtral|codestral|magistral|ministral|devstral)/.test(s)) return "Mistral";
  if (/deepseek/.test(s)) return "DeepSeek";
  if (/qwen/.test(s)) return "Qwen";
  return "Other";
}

export function vendorOfRow(row: CatalogModelRow): string {
  const p = (row.provider || "").trim();
  if (p && !AGENT_LABEL_PROVIDERS.has(p)) return p;
  return vendorFromName(row.name || row.id);
}

export function orderVendors(vendors: string[]): string[] {
  const known = VENDOR_ORDER.filter((v) => vendors.includes(v));
  const extra = vendors.filter((v) => !VENDOR_ORDER.includes(v)).sort();
  return [...known, ...extra];
}

export interface VendorGroup {
  vendor: string;
  rows: CatalogModelRow[];
}

/** Rows grouped by vendor in display order. One group ⇒ render flat, no accordion. */
export function groupByVendor(rows: CatalogModelRow[]): VendorGroup[] {
  const map = new Map<string, CatalogModelRow[]>();
  for (const row of rows) {
    const vendor = vendorOfRow(row);
    const bucket = map.get(vendor);
    if (bucket) bucket.push(row);
    else map.set(vendor, [row]);
  }
  return orderVendors([...map.keys()]).map((vendor) => ({ vendor, rows: map.get(vendor)! }));
}

export interface AgentSearchHit<A, M> {
  agent: A;
  nameHit: boolean;
  models: M[];
}

/** Live agent-list search: match agent labels and/or model names under each agent. */
export function searchAgentsAndModels<A extends { id: string; label: string }, M extends { name: string }>(
  agents: A[],
  query: string,
  modelsFor: (agentId: string) => M[],
  shortLabel?: (id: string) => string,
): AgentSearchHit<A, M>[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return agents
    .map((agent) => {
      const label = (shortLabel?.(agent.id) || agent.label).toLowerCase();
      const nameHit = label.includes(q);
      const models = modelsFor(agent.id).filter((m) => m.name.toLowerCase().includes(q));
      return { agent, nameHit, models };
    })
    .filter((h) => h.nameHit || h.models.length > 0);
}
