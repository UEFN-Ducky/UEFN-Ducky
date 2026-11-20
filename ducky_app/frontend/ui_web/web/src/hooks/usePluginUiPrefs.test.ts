// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

let push: ((event: Record<string, unknown>) => void) | null = null;
vi.mock("./usePanelApi", () => ({
  getApi: () => ({ plugin_prefs_get_all: async () => ({ ok: true, prefs: {} }), plugin_prefs_set: async () => ({}) }),
}));
vi.mock("./usePanelPushBus", () => ({
  installPanelPushBus: () => undefined,
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    push = fn;
    return () => undefined;
  },
}));
const { hydratePluginUiPrefsFromDisk, usePluginUiPrefs } = await import("./usePluginUiPrefs");

it("a plugin backend's prefs change reaches the open settings page", async () => {
  hydratePluginUiPrefsFromDisk();
  const { result } = renderHook(() => usePluginUiPrefs("ducky-auto"));
  expect(push).toBeTruthy();
  push!({ type: "plugin_prefs_changed", plugin_id: "ducky-auto", prefs: { route_coding: "claude_code:claude-opus-5-5" } });
  await waitFor(() => expect(result.current.prefs.route_coding).toBe("claude_code:claude-opus-5-5"));
});
