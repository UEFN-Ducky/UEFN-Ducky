// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { PluginScopeBar } from "./PluginScopeBar";

const api = vi.hoisted(() => ({
  plugin_scope_status: vi.fn(),
  plugin_scope_choices: vi.fn(),
  plugin_scope_set: vi.fn(),
  plugin_scope_sync: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const bar = () =>
  render(
    <ConfirmModalProvider>
      <PluginScopeBar pluginId="brainrot-tcg" onScopeChanged={() => {}} />
    </ConfirmModalProvider>,
  );

describe("plugin scope bar", () => {
  it("renders nothing for accounts without the Teams beta", async () => {
    api.plugin_scope_status.mockResolvedValue({ ok: true, visible: false, scope: { kind: "personal", label: "Local", teamId: "", readOnly: false } });
    const { container } = bar();
    await waitFor(() => expect(api.plugin_scope_status).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    expect(api.plugin_scope_sync).not.toHaveBeenCalled();
  });

  it("shows whose data it is, syncs the team, and confirms before switching", async () => {
    api.plugin_scope_status.mockResolvedValue({
      ok: true, visible: true, pluginLabel: "BrainRot TCG", email: "ana@x.org", canChange: true, members: 4, state: "ok", pending: 0,
      syncedAt: Date.now() / 1000 - 5, usage: { usedBytes: 34 * 1024 ** 2, limitBytes: 5 * 1024 ** 3 },
      scope: { kind: "team", label: "Alpha Studio", teamId: "t1", readOnly: false },
    });
    api.plugin_scope_choices.mockResolvedValue({ ok: true, choices: [
      { id: "personal", kind: "personal", label: "Local" },
      { id: "t1", kind: "team", label: "Alpha Studio", members: 4 },
    ] });
    api.plugin_scope_set.mockResolvedValue({ ok: true });
    bar();
    expect((await screen.findByText("Alpha Studio")).className).toContain("plugin-scope-bar__badge--team");
    expect(screen.getByText("shared with 4 members")).toBeTruthy();
    expect(screen.getByText("34 MB of 5 GB")).toBeTruthy();
    await waitFor(() => expect(api.plugin_scope_sync).toHaveBeenCalledWith("brainrot-tcg", false));
    expect(api.plugin_scope_status).toHaveBeenCalledWith("brainrot-tcg");

    fireEvent.click(screen.getByRole("button", { name: "Change ▾" }));
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Local" }));
    expect(await screen.findByText("Switch BrainRot TCG to Local data?")).toBeTruthy();
    expect(screen.getByText("Nothing is copied. BrainRot TCG restarts with its Local data.")).toBeTruthy();
    expect(api.plugin_scope_set).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Switch/ }));
    await waitFor(() => expect(api.plugin_scope_set).toHaveBeenCalledWith("brainrot-tcg", "personal"));
  });
});
