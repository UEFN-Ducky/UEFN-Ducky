// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const list = vi.fn();
const revoke = vi.fn();
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => ({ list_saved_agent_permissions: list, revoke_saved_agent_permission: revoke }) }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (fn: (api: unknown) => void) => {
  fn({ list_saved_agent_permissions: list }); return () => {};
} }));
const { SavedPermissionsSection } = await import("./SavedPermissionsSection");
beforeEach(() => {
  list.mockReset().mockResolvedValue([
    { conv_id: "one", title: "First", rule: "*", label: "Allow everything in this chat" },
    { conv_id: "two", title: "Second", rule: "Bash:npm run test", label: "Bash:npm run test" },
  ]);
  revoke.mockReset().mockResolvedValue({ ok: true });
});
afterEach(cleanup);

it("lists every saved approval and removes only the clicked rule", async () => {
  render(<SavedPermissionsSection />);
  await screen.findByText("First");
  expect(screen.getByText("Second")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove Allow everything in this chat from First" }));
  await waitFor(() => expect(revoke).toHaveBeenCalledWith("one", "*"));
  await screen.findByText("Permission removed.");
  expect(screen.queryByText("First")).toBeNull();
  expect(screen.getByText("Second")).toBeTruthy();
});

it("keeps the saved permission visible when revocation fails", async () => {
  revoke.mockRejectedValue(new Error("save failed"));
  render(<SavedPermissionsSection />);
  await screen.findByText("First");
  fireEvent.click(screen.getByRole("button", { name: "Remove Allow everything in this chat from First" }));
  await screen.findByText("save failed");
  expect(screen.getByText("First")).toBeTruthy();
});
