/**
 * Model picker roots = Store gateways (OpenAI / Cursor / Anthropic…), not a flat
 * Ducky+agents list. Nested CLI agents (Codex, Claude Code) hang under their plugin.
 */
import type { CodingAgentDto } from "../types/panel";
import type { PluginLlmProvider } from "../hooks/usePluginContributions";

export type PickerGateway = {
  /** Provider id (openai, anthropic, cursor, gemini…). */
  id: string;
  label: string;
  providerKey: string;
  pluginId: string;
  order: number;
  /** CLI agents nested under this gateway (Codex under OpenAI). Same-id agent omitted. */
  nestedAgents: CodingAgentDto[];
  /** When the coding agent id matches the provider (Cursor), models come from the agent. */
  primaryAgentId: string | null;
};

function norm(id: string): string {
  return (id || "").trim().toLowerCase().replace(/-/g, "_");
}

/** Build top-level picker rows from enabled llm.providers + detected coding agents. */
export function buildPickerGateways(
  providers: PluginLlmProvider[],
  agents: CodingAgentDto[],
  contribAgents: Array<{ id: string; label?: string; plugin_id?: string; order?: number }> = [],
): PickerGateway[] {
  const byPlugin = new Map<string, CodingAgentDto[]>();
  for (const a of agents) {
    if (norm(a.id) === "ducky") continue;
    const pid =
      norm((a as CodingAgentDto & { plugin_id?: string }).plugin_id || "") ||
      norm(contribAgents.find((c) => norm(c.id) === norm(a.id))?.plugin_id || "");
    if (!pid) continue;
    const list = byPlugin.get(pid) || [];
    list.push(a);
    byPlugin.set(pid, list);
  }
  // Contrib-only agents (backend not loaded yet) still nest under the gateway.
  for (const c of contribAgents) {
    const pid = norm(c.plugin_id || "");
    const cid = norm(c.id);
    if (!pid || !cid || cid === "ducky") continue;
    const list = byPlugin.get(pid) || [];
    if (list.some((a) => norm(a.id) === cid)) continue;
    list.push({
      id: cid,
      label: String(c.label || cid).trim() || cid,
      enabled: true,
      available: false,
      status: "Backend not loaded",
      models: [],
      plugin_id: pid,
    } as CodingAgentDto & { plugin_id?: string });
    byPlugin.set(pid, list);
  }

  const out: PickerGateway[] = [];
  for (const p of providers) {
    const providerKey = norm(p.id || p.secret_key || "");
    const pluginId = norm(p.plugin_id || "");
    if (!providerKey || !pluginId) continue;
    const owned = byPlugin.get(pluginId) || [];
    const primary = owned.find((a) => norm(a.id) === providerKey) || null;
    const nested = owned.filter((a) => norm(a.id) !== providerKey);
    out.push({
      id: providerKey,
      label: String(p.label || providerKey).trim() || providerKey,
      providerKey,
      pluginId,
      order: typeof p.order === "number" ? p.order : 100,
      nestedAgents: nested,
      primaryAgentId: primary ? norm(primary.id) : null,
    });
  }
  return out.sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
}

/** Which gateway owns the current chat selection (for open-to-current). */
export function gatewayForSelection(
  gateways: PickerGateway[],
  codingAgent: string,
  providerKey: string,
): PickerGateway | null {
  const agent = norm(codingAgent);
  if (agent && agent !== "ducky") {
    const hit = gateways.find(
      (g) =>
        g.primaryAgentId === agent || g.nestedAgents.some((a) => norm(a.id) === agent),
    );
    if (hit) return hit;
  }
  const pk = norm(providerKey);
  if (pk) return gateways.find((g) => g.providerKey === pk) || null;
  return null;
}
