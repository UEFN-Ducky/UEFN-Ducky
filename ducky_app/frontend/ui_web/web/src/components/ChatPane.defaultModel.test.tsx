// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const NO_MODEL = "This ducky has no model. Set a Default Model.";

// Every other bridge call answers empty (lists and loads [], everything else {}): the pane renders offline.
const overrides = vi.hoisted(() => ({
  adopt_default_model: (async () => ({})) as (...args: unknown[]) => Promise<unknown>,
  get_models_catalog: async () => ({
    models: [{ id: "claude-opus-5-5", name: "Claude Opus", provider: "anthropic", provider_key: "anthropic" }],
    default_model: "codex:gpt-6-astra",
  }),
  get_key_status: async () => ({ anthropic: true }),
}));
const api = vi.hoisted(() => new Proxy({} as Record<string, unknown>, {
  get: (_t, key) => {
    if (key === "then") return undefined;
    if (key in overrides) return overrides[key as keyof typeof overrides];
    return async () => (/^(list|load)_/.test(String(key)) ? [] : {});
  },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, requireApi: () => api }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));

import { ChatPane } from "./ChatPane";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { loadModelsCatalog } from "../hooks/modelsCatalogCache";
import type { ChatTab } from "../types/panel";

const chat = { id: "chat-1", name: "Builder", title: "Builder", codingAgent: "ducky", model: "" } as unknown as ChatTab;

function Pane() {
  return (
    <ConfirmModalProvider>
      <ChatPane chat={chat} visible allChats={[chat]} onOpenChat={() => {}} isAgentRunning={false} />
    </ConfirmModalProvider>
  );
}

beforeEach(async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  await loadModelsCatalog({ force: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("a Ducky chat with no model takes the Default Model instead of asking for one", async () => {
  const adopt = vi.fn(async () => ({ ok: true, coding_agent: "codex", model: "gpt-6-astra", provider: "" }));
  overrides.adopt_default_model = adopt;
  render(<Pane />);
  await act(async () => {});
  await waitFor(() => expect(adopt).toHaveBeenCalledWith("chat-1", false));
  await act(async () => {});
  expect(screen.queryByText(NO_MODEL)).toBeNull();
});

it("asks for a Default Model only when none is set", async () => {
  const adopt = vi.fn(async () => ({ ok: false, error: "No model selected." }));
  overrides.adopt_default_model = adopt;
  render(<Pane />);
  await act(async () => {});
  await waitFor(() => expect(adopt).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByText(NO_MODEL)).toBeTruthy());
});
