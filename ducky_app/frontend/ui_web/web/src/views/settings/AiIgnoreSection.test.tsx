// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const getSettings = vi.fn();
const saveSettings = vi.fn();
vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({ get_settings: getSettings, save_agent_settings: saveSettings }),
}));
vi.mock("../../hooks/onApiReady", () => ({
  onApiReady: (fn: () => void) => { fn(); return () => {}; },
}));
vi.mock("../../ui-targets/registry", () => ({ useUiTarget: () => ({ current: null }) }));
const { AiIgnoreSection } = await import("./AiIgnoreSection");

beforeEach(() => {
  getSettings.mockReset().mockResolvedValue({ ai_ignore_patterns: ["secrets/"], ai_ignore_strict: true });
  saveSettings.mockReset().mockResolvedValue("Saved AI ignore list.");
});
afterEach(cleanup);

it("loads persisted rules and saves user edits with the protection mode", async () => {
  render(<AiIgnoreSection />);
  const input = screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement;
  await waitFor(() => expect(input.value).toBe("secrets/"));
  expect(screen.getByText(".env")).toBeTruthy();
  expect(screen.getByText(".env.*")).toBeTruthy();
  fireEvent.change(input, { target: { value: "secrets/\n *.key \n" } });
  fireEvent.click(screen.getByText("Save ignore list"));
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({
    ai_ignore_patterns: ["secrets/", "*.key"], ai_ignore_strict: true,
  }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Ignore list saved."));
});

it("shows a rejected save without claiming protection was updated", async () => {
  saveSettings.mockRejectedValue(new Error("Stop active agents before changing AI file protection."));
  render(<AiIgnoreSection />);
  await waitFor(() => expect((screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Save ignore list"));
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Stop active agents"));
  expect(screen.queryByText("Ignore list saved.")).toBeNull();
});

it("keeps editing disabled when settings cannot be loaded", async () => {
  getSettings.mockRejectedValue(new Error("offline"));
  render(<AiIgnoreSection />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Could not load"));
  expect((screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement).disabled).toBe(true);
});
