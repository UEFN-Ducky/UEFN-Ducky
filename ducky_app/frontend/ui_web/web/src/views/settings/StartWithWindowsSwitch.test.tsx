// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  get_settings: vi.fn(),
  save_agent_settings: vi.fn(async () => ({ ok: true })),
};

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../hooks/onApiReady", () => ({
  onApiReady: (fn: () => void) => {
    fn();
    return () => undefined;
  },
}));

import { StartWithWindowsSwitch } from "./AddToUefnTab";

const box = () => screen.getByLabelText("Start with Windows") as HTMLInputElement;

beforeEach(() => {
  api.get_settings.mockReset();
  api.save_agent_settings.mockClear();
});
afterEach(() => cleanup());

describe("Start with Windows switch", () => {
  it("is on when the setting was never saved", async () => {
    api.get_settings.mockResolvedValue({});
    render(<StartWithWindowsSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    expect(box().checked).toBe(true);
  });

  it("shows off when switched off, and saves a change", async () => {
    api.get_settings.mockResolvedValue({ start_with_windows: false });
    render(<StartWithWindowsSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    expect(box().checked).toBe(false);
    await act(async () => {
      fireEvent.click(box());
    });
    expect(api.save_agent_settings).toHaveBeenCalledWith({ start_with_windows: true });
    expect(box().checked).toBe(true);
  });
});
