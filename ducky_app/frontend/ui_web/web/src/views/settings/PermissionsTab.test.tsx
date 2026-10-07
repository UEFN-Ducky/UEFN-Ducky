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
vi.mock("./AiIgnoreSection", () => ({ AiIgnoreSection: () => <div>File rules</div> }));
vi.mock("./AgentTab", () => ({ WebAccessSection: () => <div>Web controls</div> }));
vi.mock("../../voice/audioSettings", () => ({
  loadAudioSettings: async () => ({ micPermission: "ask" }),
  subscribeAudioSettings: () => () => {},
  getAudioSettings: () => ({ micPermission: "ask" }),
  saveAudioSettings: vi.fn(),
}));
const { PermissionsTab } = await import("./PermissionsTab");
beforeEach(() => {
  getSettings.mockReset().mockResolvedValue({
    allow_settings_write: false, allow_agent_clicks: true,
    allow_see_uefn: false, allow_see_other_programs: true,
  });
  saveSettings.mockReset().mockResolvedValue("Saved settings.");
});
afterEach(cleanup);

it("loads and saves all AI access permissions alongside file, web and microphone controls", async () => {
  render(<PermissionsTab />);
  const settings = screen.getByLabelText("Allow AI settings changes") as HTMLInputElement;
  await waitFor(() => expect(settings.disabled).toBe(false));
  expect(settings.checked).toBe(false);
  expect((screen.getByLabelText("Allow AI clicks") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByLabelText("Allow AI to see UEFN") as HTMLInputElement).checked).toBe(false);
  expect((screen.getByLabelText("Allow AI to see other programs") as HTMLInputElement).checked).toBe(true);
  expect(screen.getByText("File rules")).toBeTruthy();
  expect(screen.getByText("Web controls")).toBeTruthy();
  expect(screen.getByLabelText("Microphone permission")).toBeTruthy();
  fireEvent.click(settings);
  fireEvent.click(screen.getByText("Save AI access permissions"));
  await waitFor(() => expect(saveSettings).toHaveBeenCalledWith({
    allow_settings_write: true, allow_agent_clicks: true,
    allow_see_uefn: false, allow_see_other_programs: true,
  }));
  await screen.findByText("AI access permissions saved.");
});

it("keeps permissions disabled if loading fails and reports rejected saves", async () => {
  getSettings.mockRejectedValueOnce(new Error("offline"));
  const view = render(<PermissionsTab />);
  await screen.findByText("Could not load AI access permissions.");
  expect((screen.getByLabelText("Allow AI clicks") as HTMLInputElement).disabled).toBe(true);
  view.unmount();
  saveSettings.mockResolvedValueOnce("Permission save refused");
  render(<PermissionsTab />);
  await waitFor(() => expect((screen.getByLabelText("Allow AI clicks") as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Save AI access permissions"));
  await screen.findByText("Permission save refused");
  expect(screen.queryByText("AI access permissions saved.")).toBeNull();
});
