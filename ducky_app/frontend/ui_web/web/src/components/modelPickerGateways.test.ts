import { describe, expect, it } from "vitest";
import { buildPickerGateways, gatewayForSelection } from "./modelPickerGateways";
import type { CodingAgentDto } from "../types/panel";
import type { PluginLlmProvider } from "../hooks/usePluginContributions";

const providers: PluginLlmProvider[] = [
  {
    id: "openai",
    label: "OpenAI",
    kind: "secret",
    secret_key: "openai",
    order: 20,
    plugin_id: "openai",
  },
  {
    id: "cursor",
    label: "Cursor",
    kind: "secret",
    secret_key: "cursor",
    order: 5,
    plugin_id: "cursor",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    kind: "secret",
    secret_key: "anthropic",
    order: 10,
    plugin_id: "anthropic",
  },
];

const agents: CodingAgentDto[] = [
  { id: "ducky", label: "Ducky", enabled: true, available: true, status: "ok" },
  {
    id: "codex",
    label: "Codex",
    enabled: true,
    available: true,
    status: "ok",
    plugin_id: "openai",
    models: [{ id: "gpt-5", name: "GPT-5" }],
  },
  {
    id: "cursor",
    label: "Cursor",
    enabled: true,
    available: true,
    status: "ok",
    plugin_id: "cursor",
    models: [{ id: "auto", name: "Auto" }],
  },
  {
    id: "claude_code",
    label: "Claude Code",
    enabled: true,
    available: true,
    status: "ok",
    plugin_id: "anthropic",
  },
];

describe("buildPickerGateways", () => {
  it("nests Codex under OpenAI and keeps Cursor as primary agent", () => {
    const gws = buildPickerGateways(providers, agents);
    expect(gws.map((g) => g.id)).toEqual(["cursor", "anthropic", "openai"]);
    const openai = gws.find((g) => g.id === "openai")!;
    expect(openai.nestedAgents.map((a) => a.id)).toEqual(["codex"]);
    expect(openai.primaryAgentId).toBeNull();
    const cursor = gws.find((g) => g.id === "cursor")!;
    expect(cursor.primaryAgentId).toBe("cursor");
    expect(cursor.nestedAgents).toEqual([]);
    const anthropic = gws.find((g) => g.id === "anthropic")!;
    expect(anthropic.nestedAgents.map((a) => a.id)).toEqual(["claude_code"]);
  });

  it("resolves selection to parent gateway", () => {
    const gws = buildPickerGateways(providers, agents);
    expect(gatewayForSelection(gws, "codex", "")?.id).toBe("openai");
    expect(gatewayForSelection(gws, "ducky", "anthropic")?.id).toBe("anthropic");
    expect(gatewayForSelection(gws, "cursor", "")?.id).toBe("cursor");
  });
});
