import { describe, expect, it } from "vitest";
import type { CatalogModelRow } from "../hooks/modelsCatalogCache";
import {
  groupByVendor,
  orderVendors,
  searchAgentsAndModels,
  vendorFromName,
  vendorOfRow,
} from "./modelVendors";

function row(partial: Partial<CatalogModelRow> & Pick<CatalogModelRow, "id" | "name" | "provider">): CatalogModelRow {
  return {
    providerKey: "",
    supportsVision: false,
    supportsTools: true,
    supportsWebSearch: false,
    contextLimit: 0,
    priceIn: null,
    priceOut: null,
    isLocal: false,
    ...partial,
  };
}

describe("vendorFromName", () => {
  it("classifies the coding-agent model names from the Cursor list", () => {
    const cases: Array<[string, string]> = [
      ["Opus 4.8", "Anthropic"],
      ["Sonnet 5", "Anthropic"],
      ["Fable 5", "Anthropic"],
      ["Haiku 4.5", "Anthropic"],
      ["GPT-5.6 Sol", "OpenAI"],
      ["Codex 5.3", "OpenAI"],
      ["Gemini 3.1 Pro", "Google"],
      ["Cursor Grok 4.5", "xAI"],
      ["Composer 2.5", "Cursor"],
      ["Auto", "Cursor"],
      ["Kimi K2.7 Code", "Moonshot"],
      ["GLM 5.2", "Zhipu"],
    ];
    for (const [name, vendor] of cases) {
      expect(vendorFromName(name), name).toBe(vendor);
    }
  });

  it("falls back to Other for unknown makers", () => {
    expect(vendorFromName("Totally New Model 9000")).toBe("Other");
  });
});

describe("vendorOfRow", () => {
  it("uses the real provider for Ducky catalog rows", () => {
    expect(vendorOfRow(row({ id: "claude-opus-4-8", name: "Claude Opus 4.8", provider: "Anthropic" }))).toBe(
      "Anthropic",
    );
    expect(vendorOfRow(row({ id: "gpt-5.5", name: "GPT-5.5", provider: "OpenAI" }))).toBe("OpenAI");
  });

  it("derives the vendor when the provider is just the coding-agent label", () => {
    // Cursor reports every model under provider "Cursor" — derive from the name.
    expect(vendorOfRow(row({ id: "gpt-5.6-sol", name: "GPT-5.6 Sol", provider: "Cursor" }))).toBe("OpenAI");
    expect(vendorOfRow(row({ id: "opus-4.8", name: "Opus 4.8", provider: "Cursor" }))).toBe("Anthropic");
    expect(vendorOfRow(row({ id: "composer-2.5", name: "Composer 2.5", provider: "Cursor" }))).toBe("Cursor");
  });
});

describe("orderVendors", () => {
  it("orders known vendors by preference and appends unknowns alphabetically", () => {
    expect(orderVendors(["Zhipu", "Anthropic", "Zebra", "OpenAI", "Acme"])).toEqual([
      "Anthropic",
      "OpenAI",
      "Zhipu",
      "Acme",
      "Zebra",
    ]);
  });
});

describe("groupByVendor", () => {
  it("splits a flat Cursor list into ordered per-maker groups", () => {
    const rows = [
      row({ id: "auto", name: "Auto", provider: "Cursor" }),
      row({ id: "gpt-5.5", name: "GPT-5.5", provider: "Cursor" }),
      row({ id: "opus-4.8", name: "Opus 4.8", provider: "Cursor" }),
      row({ id: "grok-4.5", name: "Cursor Grok 4.5", provider: "Cursor" }),
      row({ id: "composer-2.5", name: "Composer 2.5", provider: "Cursor" }),
    ];
    const groups = groupByVendor(rows);
    expect(groups.map((g) => g.vendor)).toEqual(["Anthropic", "OpenAI", "xAI", "Cursor"]);
    expect(groups.find((g) => g.vendor === "Cursor")!.rows.map((r) => r.id)).toEqual(["auto", "composer-2.5"]);
  });

  it("keeps a single-vendor list as one group (rendered flat, no accordion)", () => {
    const rows = [
      row({ id: "sonnet", name: "Sonnet (latest)", provider: "Claude Code" }),
      row({ id: "opus", name: "Opus (latest)", provider: "Claude Code" }),
      row({ id: "fable", name: "Fable (latest)", provider: "Claude Code" }),
    ];
    const groups = groupByVendor(rows);
    expect(groups).toHaveLength(1);
    expect(groups[0].vendor).toBe("Anthropic");
    expect(groups[0].rows).toHaveLength(3);
  });
});

describe("searchAgentsAndModels", () => {
  const agents = [
    { id: "ducky", label: "Ducky" },
    { id: "cursor", label: "Cursor" },
    { id: "codex", label: "Codex" },
  ];
  const modelsFor = (id: string) => {
    if (id === "cursor") {
      return [
        { name: "Auto" },
        { name: "Composer 2.5" },
        { name: "Composer 2" },
      ];
    }
    if (id === "codex") return [{ name: "GPT-5.3 Codex" }];
    return [{ name: "Sonnet 5" }];
  };

  it("finds nested models before any agent is opened", () => {
    const hits = searchAgentsAndModels(agents, "composer", modelsFor);
    expect(hits).toHaveLength(1);
    expect(hits[0].agent.id).toBe("cursor");
    expect(hits[0].nameHit).toBe(false);
    expect(hits[0].models.map((m) => m.name)).toEqual(["Composer 2.5", "Composer 2"]);
  });

  it("still matches agent labels", () => {
    const hits = searchAgentsAndModels(agents, "cod", modelsFor);
    expect(hits.map((h) => h.agent.id).sort()).toEqual(["codex"]);
    expect(hits[0].nameHit).toBe(true);
  });
});
