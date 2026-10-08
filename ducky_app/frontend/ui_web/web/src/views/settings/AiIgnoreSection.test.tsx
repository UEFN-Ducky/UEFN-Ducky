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

it("loads persisted rules and automatically saves edits on blur without changing protection", async () => {
  render(<AiIgnoreSection />);
  const input = screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement;
  await waitFor(() => expect(input.value).toBe("secrets/"));
  expect(screen.getByText(".env")).toBeTruthy();
  expect(screen.getByText(".env.*")).toBeTruthy();
  fireEvent.change(input, { target: { value: "secrets/\n *.key \n" } });
  fireEvent.blur(input);
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({
    ai_ignore_patterns: ["secrets/", "*.key"],
  }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Saved."));
});

it("shows a rejected save without claiming protection was updated", async () => {
  saveSettings.mockRejectedValue(new Error("Stop active agents before changing AI file protection."));
  render(<AiIgnoreSection />);
  await waitFor(() => expect((screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("checkbox", { name: "Strict protection" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Stop active agents"));
  expect(screen.queryByText("Saved.")).toBeNull();
  expect((screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement).checked).toBe(true);
});

it.each([undefined, false, true])("preserves the saved protection choice (%s) without enabling it on upgrade", async (strict) => {
  getSettings.mockResolvedValue({ ai_ignore_patterns: ["secrets/"], ai_ignore_strict: strict });
  render(<AiIgnoreSection />);
  const toggle = screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement;
  await waitFor(() => expect(toggle.disabled).toBe(false));
  expect(toggle.checked).toBe(strict === true);
  expect(saveSettings).not.toHaveBeenCalled();
});

it("lets the user opt into strict protection", async () => {
  getSettings.mockResolvedValue({ ai_ignore_patterns: [] });
  render(<AiIgnoreSection />);
  const toggle = screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement;
  await waitFor(() => expect(toggle.disabled).toBe(false));
  fireEvent.click(toggle);
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({
    ai_ignore_strict: true,
  }));
});

it("turns strict protection off on click and keeps it off after reopening", async () => {
  const view = render(<AiIgnoreSection />);
  const toggle = screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement;
  await waitFor(() => expect(toggle.disabled).toBe(false));
  fireEvent.click(toggle);
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ ai_ignore_strict: false }));
  await screen.findByText("Saved.");
  view.unmount();
  getSettings.mockResolvedValue({ ai_ignore_patterns: ["secrets/"], ai_ignore_strict: false });
  render(<AiIgnoreSection />);
  await waitFor(() => expect((screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement).disabled).toBe(false));
  expect((screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement).checked).toBe(false);
});

it("does not lose a strict-toggle click while file rules are saving", async () => {
  let finishRules: ((value: string) => void) | undefined;
  saveSettings.mockImplementationOnce(() => new Promise<string>((resolve) => { finishRules = resolve; }));
  render(<AiIgnoreSection />);
  const input = screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  fireEvent.change(input, { target: { value: "private/" } });
  fireEvent.blur(input);
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ ai_ignore_patterns: ["private/"] }));
  const toggle = screen.getByRole("checkbox", { name: "Strict protection" }) as HTMLInputElement;
  expect(toggle.disabled).toBe(false);
  fireEvent.click(toggle);
  finishRules?.("Saved AI ignore list.");
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({ ai_ignore_strict: false }));
  await screen.findByText("Saved.");
  expect(toggle.checked).toBe(false);
});

it("keeps editing disabled when settings cannot be loaded", async () => {
  getSettings.mockRejectedValue(new Error("offline"));
  render(<AiIgnoreSection />);
  await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Could not load"));
  expect((screen.getByLabelText("Additional protected files and folders") as HTMLTextAreaElement).disabled).toBe(true);
});
