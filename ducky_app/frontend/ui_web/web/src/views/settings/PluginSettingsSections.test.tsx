// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const pluginCall = vi.fn();
let api: Record<string, unknown> = {};
let sections: unknown[] = [];
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../hooks/usePluginContributions", async (importActual) => {
  const actual = await importActual<typeof import("../../hooks/usePluginContributions")>();
  return { ...actual, usePluginContributions: () => ({ settings_sections: sections }) };
});
vi.mock("../../hooks/usePluginUiPrefs", () => ({
  usePluginUiPrefs: () => ({ prefs: {}, setPref: vi.fn(), setPrefs: vi.fn(), getBool: () => false }),
}));
vi.mock("./PluginWalkthroughReplayButton", () => ({ PluginWalkthroughReplayButton: () => null }));
const pickerProps: Array<Record<string, unknown>> = [];
vi.mock("../../components/ducky/DuckyModelPicker", () => ({
  DuckyModelPicker: (props: Record<string, unknown>) => {
    pickerProps.push(props);
    return <span>{String(props.placeholder)}</span>;
  },
}));
const { PluginSettingsSections } = await import("./PluginSettingsSections");

const account = {
  id: "account",
  tab: "Notion",
  plugin_id: "notion",
  title: "Notion account",
  order: 10,
  properties: [
    {
      id: "account",
      type: "action",
      label: "Sign in with Notion",
      rpc: "connect",
      status_rpc: "status",
      connected_label: "Disconnect",
      connected_rpc: "disconnect",
    },
  ],
};

beforeEach(() => {
  pluginCall.mockReset();
  api = { plugin_call: pluginCall };
  sections = [account];
});
afterEach(cleanup);

it("reads the status on mount, signs in, then offers to disconnect", async () => {
  pluginCall.mockImplementation(async (_pid: string, method: string) =>
    method === "status"
      ? { ok: true, connected: false, detail: "Not connected" }
      : { ok: true, connected: true, detail: "Connected to Studio" },
  );
  render(<PluginSettingsSections tabId="Notion" />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Not connected"));
  expect(pluginCall).toHaveBeenCalledWith("notion", "status", {});

  fireEvent.click(screen.getByRole("button", { name: "Sign in with Notion" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Connected to Studio"));
  expect(pluginCall).toHaveBeenCalledWith("notion", "connect", {});

  pluginCall.mockResolvedValue({ ok: true, connected: false, detail: "Not connected" });
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Sign in with Notion" })).toBeTruthy());
  expect(pluginCall).toHaveBeenLastCalledWith("notion", "disconnect", {});
});

it("shows a failed sign-in as an error and keeps the sign-in button", async () => {
  pluginCall.mockImplementation(async (_pid: string, method: string) =>
    method === "status"
      ? { ok: true, connected: false, detail: "Not connected" }
      : { ok: false, connected: false, detail: "Notion sign-in timed out" },
  );
  render(<PluginSettingsSections tabId="Notion" />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Not connected"));
  fireEvent.click(screen.getByRole("button", { name: "Sign in with Notion" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Notion sign-in timed out"));
  expect(screen.getByRole("status").className).toContain("is-error");
  expect(screen.getByRole("button", { name: "Sign in with Notion" })).toBeTruthy();
});

it("asks for an app update when the bridge has no plugin_call", async () => {
  api = {};
  render(<PluginSettingsSections tabId="Notion" />);
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toBe("Update the app to use this button"),
  );
});

it("renders a model property with the chat's model picker, API models only", () => {
  pickerProps.length = 0;
  sections = [
    {
      id: "routes",
      tab: "LLMs",
      plugin_id: "ducky-auto",
      title: "Model per task",
      properties: [
        { id: "route_coding", type: "model", label: "Coding", placeholder: "Auto", hide_providers: ["ducky_auto"] },
      ],
    },
  ];
  render(<PluginSettingsSections tabId="LLMs" />);
  expect(screen.getByText("Coding")).toBeTruthy();
  expect(screen.getByText("Auto")).toBeTruthy();
  expect(pickerProps.at(-1)).toMatchObject({ model: "", apiOnly: true, hideProviders: ["ducky_auto"] });
});

it("a model property with logins offers the chat's logins and the Faster / Smarter slider", () => {
  pickerProps.length = 0;
  sections = [
    {
      id: "coding",
      tab: "LLMs",
      plugin_id: "ducky-auto",
      title: "Coding",
      properties: [{ id: "route_coding", type: "model", label: "Model", placeholder: "Auto", logins: true }],
    },
  ];
  render(<PluginSettingsSections tabId="LLMs" />);
  const props = pickerProps.at(-1)!;
  expect(props.apiOnly).toBeUndefined();
  expect(props.thinkingEffort).toBe("off");
  expect(typeof props.onEffortChange).toBe("function");
});

it("renders a collapsed section as a closed accordion", async () => {
  pluginCall.mockResolvedValue({ ok: true, connected: false, detail: "Not connected" });
  sections = [
    account,
    {
      id: "advanced",
      tab: "Notion",
      plugin_id: "notion",
      title: "Advanced",
      collapsed: true,
      order: 30,
      properties: [{ id: "notion_token", type: "secret", label: "Internal integration token" }],
    },
  ];
  const { container } = render(<PluginSettingsSections tabId="Notion" />);
  const details = container.querySelector("details");
  expect(details).toBeTruthy();
  expect(details!.open).toBe(false);
  expect(details!.textContent).toContain("Advanced");
  expect(details!.textContent).toContain("Internal integration token");
  expect(details!.textContent).not.toContain("Sign in with Notion");
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Not connected"));
});
