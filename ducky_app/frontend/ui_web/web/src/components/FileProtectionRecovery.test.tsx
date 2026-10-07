// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const save = vi.fn();
const navigate = vi.fn();
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({ save_agent_settings: save }) }));
vi.mock("../navigation/openPanelRoute", () => ({ openPanelRoute: navigate }));
const { FileProtectionRecovery } = await import("./FileProtectionRecovery");
beforeEach(() => {
  save.mockReset().mockResolvedValue("Saved AI ignore list.");
  navigate.mockReset();
});
afterEach(cleanup);

it("turns strict protection off only when the user clicks and waits for persistence", async () => {
  render(<FileProtectionRecovery />);
  expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Turn off Strict protection" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ ai_ignore_strict: false }));
  await screen.findByRole("button", { name: "Strict protection off" });
  expect(screen.getByRole("status").textContent).toContain("You can continue this chat");
});

it("reports a refusal and leaves the action available", async () => {
  save.mockRejectedValue(new Error("Stop active agents before changing AI file protection."));
  render(<FileProtectionRecovery />);
  fireEvent.click(screen.getByRole("button", { name: "Turn off Strict protection" }));
  await screen.findByText(/Stop active agents/);
  expect((screen.getByRole("button", { name: "Turn off Strict protection" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByText("Strict protection off")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Permissions and rules" }));
  expect(navigate).toHaveBeenCalledWith("settings.permissions");
});
