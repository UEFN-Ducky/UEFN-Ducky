// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  get_settings: vi.fn(),
  duckyos_get_status: vi.fn(),
  duckyos_list_pcs: vi.fn(),
  remote_status: vi.fn(),
  remote_set_enabled: vi.fn(),
  remote_sign_out_all: vi.fn(),
  duckyos_logout: vi.fn(),
}));
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("./PluginWalkthroughReplayButton", () => ({ PluginWalkthroughReplayButton: () => null }));
import { AccountTab } from "./AccountTab";

beforeEach(() => {
  vi.resetAllMocks();
  api.get_settings.mockResolvedValue({});
  api.duckyos_get_status.mockResolvedValue({
    logged_in: true, device_key_active: true, display_name: "Taylor", email: "taylor@example.com",
  });
  api.duckyos_list_pcs.mockResolvedValue({ devices: [
    { keyId: "other", name: "Other PC", mine: false },
    { keyId: "mine", name: "Studio PC", mine: true },
  ] });
  api.remote_status.mockResolvedValue({ enabled: true, running: true, sessions: 2 });
});
afterEach(cleanup);

it("shows account controls and active browser sessions after login", async () => {
  render(<AccountTab />);
  await screen.findByText("Enabled");
  expect(screen.getByText("Taylor")).toBeTruthy();
  expect(screen.getByText("taylor@example.com")).toBeTruthy();
  expect(screen.getByText("Studio PC")).toBeTruthy();
  expect(screen.queryByText("Other PC")).toBeNull();
  expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(["Disable", "Log out", "Disconnect all sessions"]);
  expect(screen.getByText("2 active")).toBeTruthy();
  expect(screen.getByText("Browser session 1")).toBeTruthy();
  expect(screen.getByText("Browser session 2")).toBeTruthy();
  expect(screen.queryByText("AI permissions")).toBeNull();
});

it("disconnects all sessions without disabling access or logging out", async () => {
  api.remote_status.mockResolvedValue({ enabled: true, running: true, sessions: 1, session_list: [{ n: 1, expires_in_s: 90 }] });
  api.remote_sign_out_all.mockResolvedValue({ enabled: true, running: true, sessions: 0, session_list: [] });
  render(<AccountTab />);
  await screen.findByText("Expires in 2 min");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect all sessions" }));
  await screen.findByText("No active browser sessions.");
  expect(screen.getByText("0 active")).toBeTruthy();
  expect(screen.getByText("Taylor")).toBeTruthy();
  expect(screen.getByText("Enabled")).toBeTruthy();
  expect(api.remote_sign_out_all).toHaveBeenCalledTimes(1);
  expect(api.duckyos_logout).not.toHaveBeenCalled();
  expect(api.remote_set_enabled).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Disconnect all sessions" }) as HTMLButtonElement).disabled).toBe(true);
});

it("retains sessions and allows retry when disconnect fails", async () => {
  api.remote_sign_out_all.mockResolvedValue({ ok: false, error: "Could not disconnect" });
  render(<AccountTab />);
  await screen.findByText("2 active");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect all sessions" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Could not disconnect");
  expect(screen.getByText("2 active")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Disconnect all sessions" }) as HTMLButtonElement).disabled).toBe(false);
});

it("disables access without logging out and can enable it again", async () => {
  api.remote_set_enabled.mockImplementation(async (enabled: boolean) => {
    const status = { enabled, running: enabled };
    api.remote_status.mockResolvedValue(status);
    return status;
  });
  render(<AccountTab />);
  await screen.findByText("Enabled");
  fireEvent.click(screen.getByRole("button", { name: "Disable" }));
  await screen.findByText("Disabled");
  expect(api.remote_set_enabled).toHaveBeenCalledWith(false);
  expect(screen.getByText("Taylor")).toBeTruthy();
  expect(api.duckyos_logout).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await screen.findByText("Enabled");
  expect(api.remote_set_enabled).toHaveBeenLastCalledWith(true);
});

it("uses the single logout action and returns to sign-in", async () => {
  api.duckyos_logout.mockResolvedValue({ logged_in: false });
  render(<AccountTab />);
  fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
  await screen.findByRole("button", { name: "Open Profile" });
  expect(api.duckyos_logout).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Taylor")).toBeNull();
  expect(screen.queryByText("AI permissions")).toBeNull();
});

it("keeps the current state and reports a failed access change", async () => {
  api.remote_set_enabled.mockResolvedValue({ ok: false, error: "Connection unavailable" });
  render(<AccountTab />);
  await screen.findByText("Enabled");
  fireEvent.click(screen.getByRole("button", { name: "Disable" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Connection unavailable");
  await waitFor(() => expect((screen.getByRole("button", { name: "Disable" }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByText("Enabled")).toBeTruthy();
});
