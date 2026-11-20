// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Claude Opus is listed under two gateways: Anthropic (API key) and Cursor. Kimi is signed in
// but offers no API models here.
const api = vi.hoisted(() => new Proxy({} as Record<string, unknown>, {
  get: (_t, key) => {
    if (key === "then") return undefined;
    if (key === "get_models_catalog") {
      return async () => ({
        models: [
          { id: "claude-opus-5-5", name: "Claude Opus 5.5", provider: "Cursor", provider_key: "cursor" },
          { id: "claude-opus-5-5", name: "Claude Opus 5.5", provider: "Anthropic", provider_key: "anthropic" },
        ],
      });
    }
    return async () => (/^(list|load)_/.test(String(key)) ? [] : {});
  },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, requireApi: () => api }));
vi.mock("../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({
    llm_providers: [
      { id: "cursor", label: "Cursor", plugin_id: "cursor" },
      { id: "anthropic", label: "Anthropic", plugin_id: "anthropic" },
      { id: "kimi", label: "Kimi", plugin_id: "kimi" },
    ],
    llm_coding_agents: [],
  }),
}));

import { ModelSelector } from "./ModelSelector";
import { loadModelsCatalog } from "../hooks/modelsCatalogCache";

beforeEach(async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  await loadModelsCatalog({ force: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function open(props: Record<string, unknown>) {
  const { container } = render(
    <ModelSelector selectedModel="claude-opus-5-5" setSelectedModel={() => undefined} apiOnly preserveSelection {...props} />,
  );
  await act(async () => {});
  fireEvent.click(container.querySelector(".model-selector-btn")!);
  await act(async () => {});
}

// Feedback (Nov 20 2026): Ducky Auto's picker kept opening on Cursor and saving cursor:claude-opus
// when Anthropic was picked, and showed Anthropic with "No models" when it had no API key.
it("opens on the picked gateway and hands back the gateway a model was picked under", async () => {
  const picked = vi.fn();
  await open({ selectedProvider: "anthropic", setSelectedModel: picked });
  expect(screen.getByRole("button", { name: "Open Anthropic settings" })).toBeTruthy();
  fireEvent.click(screen.getByText("Claude Opus 5.5"));
  expect(picked).toHaveBeenCalledWith("claude-opus-5-5", "anthropic");
});

it("leaves out gateways that have no API models", async () => {
  await open({});
  fireEvent.click(screen.getByRole("button", { name: "Back to gateways" }));
  expect(screen.queryByText("Kimi")).toBeNull();
  expect(screen.getAllByText("Anthropic").length).toBeGreaterThan(0);
});
