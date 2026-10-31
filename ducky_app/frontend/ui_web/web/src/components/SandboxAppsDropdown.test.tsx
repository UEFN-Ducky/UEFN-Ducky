// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SandboxApp } from "../types/panel";

const api = vi.hoisted(() => ({
  sandbox_apps: vi.fn(),
  sandbox_relaunch: vi.fn(async () => ({ ok: true })),
  sandbox_stop_program: vi.fn(async () => ({ ok: true })),
  sandbox_delete: vi.fn(async () => ({ ok: true })),
  plugin_call: vi.fn(),
  open_external_url: vi.fn(),
}));
const confirm = vi.hoisted(() => vi.fn(async () => true));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../contexts/ConfirmModalContext", () => ({ confirmFromHost: confirm }));

import { SandboxAppsDropdown } from "./SandboxAppsDropdown";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const app = (over: Partial<SandboxApp>): SandboxApp => ({
  conv_id: "c1", name: "web", chat: "Build a todo app", running: false, pid: 0, ports: [], launched: 1, picture: "", ...over,
});

it("always shows its button; with no apps the menu says so, and a press outside closes it", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [] });
  render(<SandboxAppsDropdown />);
  await waitFor(() => expect(api.sandbox_apps).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "Apps" }));
  expect(screen.getByText("No apps yet")).toBeTruthy();
  expect(screen.queryByLabelText("Search apps")).toBeNull();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByText("No apps yet")).toBeNull();
});

it("runs a stopped app, stops, opens and deletes a running one", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [
    app({ name: "web", running: true, pid: 42, ports: [5173], picture: "http://x/shot.png" }),
    app({ conv_id: "c2", name: "game" }),
  ] });
  render(<SandboxAppsDropdown />);
  fireEvent.click(await screen.findByRole("button", { name: "Apps · 1 running" }));

  fireEvent.click(screen.getByRole("button", { name: "Run game" }));
  expect(api.sandbox_relaunch).toHaveBeenCalledWith("c2", "game");
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop web" })).not.toHaveProperty("disabled", true));
  fireEvent.click(screen.getByRole("button", { name: "Stop web" }));
  expect(api.sandbox_stop_program).toHaveBeenCalledWith("c1", 42);

  fireEvent.click(screen.getByRole("button", { name: "Open web in your browser" }));
  expect(api.open_external_url).toHaveBeenCalledWith("http://localhost:5173");

  fireEvent.click(screen.getByRole("button", { name: "Delete web" }));
  await waitFor(() => expect(api.sandbox_delete).toHaveBeenCalledWith("c1"));
});

it("filters by running state and searches names and chats", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [
    app({ name: "web", running: true, pid: 1, ports: [5173] }),
    app({ conv_id: "c2", name: "game", chat: "Platformer" }),
  ] });
  render(<SandboxAppsDropdown />);
  fireEvent.click(await screen.findByRole("button", { name: /Apps/ }));
  fireEvent.click(screen.getByRole("button", { name: /Stopped/ }));
  expect(screen.queryByText("web")).toBeNull();
  expect(screen.getByText("game")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /All/ }));
  fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "platf" } });
  expect(screen.queryByText("web")).toBeNull();
  expect(screen.getByText("game")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "zzz" } });
  expect(screen.getByText("No apps match.")).toBeTruthy();
});

it("ticks several apps (Shift for a range) and stops or deletes them together", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [
    app({ conv_id: "c1", name: "web", running: true, pid: 7, ports: [5173] }),
    app({ conv_id: "c2", name: "game" }),
    app({ conv_id: "c3", name: "site" }),
    app({ conv_id: "c3", name: "site-2" }),
  ] });
  render(<SandboxAppsDropdown />);
  fireEvent.click(await screen.findByRole("button", { name: /Apps/ }));
  fireEvent.click(screen.getByLabelText("Select web"));
  fireEvent.click(screen.getByLabelText("Select site"), { shiftKey: true });
  expect(screen.getByText("3 selected")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: /Stop$/ }));
  await waitFor(() => expect(api.sandbox_stop_program).toHaveBeenCalledWith("c1", 7));
  expect(api.sandbox_stop_program).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "Delete 3" }));
  await waitFor(() => expect(api.sandbox_delete).toHaveBeenCalledTimes(3));
  expect(api.sandbox_delete.mock.calls.map((c) => (c as unknown[])[0])).toEqual(["c1", "c2", "c3"]);
  // The confirm names the sandbox-mate that goes too.
  expect((confirm.mock.calls[0] as unknown[])[0]).toMatchObject({ message: expect.stringContaining("site-2") });
  await waitFor(() => expect(screen.queryByText(/selected$/)).toBeNull());
});

it("Select all ticks every shown app and the X clears it", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [app({ name: "a" }), app({ conv_id: "c2", name: "b" })] });
  render(<SandboxAppsDropdown />);
  fireEvent.click(await screen.findByRole("button", { name: /Apps/ }));
  fireEvent.click(screen.getByLabelText("Select a"));
  fireEvent.click(screen.getByRole("button", { name: "Select all" }));
  expect(screen.getByText("2 selected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
  expect(screen.queryByText(/selected$/)).toBeNull();
});

it("shows the app's favicon, then its screenshot when there is none", async () => {
  api.sandbox_apps.mockResolvedValue({ ok: true, apps: [app({ running: true, pid: 1, ports: [8000], picture: "http://x/shot.png" })] });
  render(<SandboxAppsDropdown />);
  fireEvent.click(await screen.findByRole("button", { name: /Apps/ }));
  const img = document.querySelector("img.sandbox-app-icon") as HTMLImageElement;
  expect(img.getAttribute("src")).toBe("http://localhost:8000/favicon.ico");
  fireEvent.error(img);
  expect((document.querySelector("img.sandbox-app-icon") as HTMLImageElement).getAttribute("src")).toBe("http://x/shot.png");
  fireEvent.click(screen.getByTitle("Open localhost:8000 in the Browser tab"));
  expect(api.plugin_call).toHaveBeenCalledWith("browser", "open_page", { url: "localhost:8000" });
});
