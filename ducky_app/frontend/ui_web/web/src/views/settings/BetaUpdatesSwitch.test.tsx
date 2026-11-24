// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  get_settings: vi.fn(),
  save_agent_settings: vi.fn(async () => "Saved"),
};

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../hooks/onApiReady", () => ({
  onApiReady: (fn: () => void) => {
    fn();
    return () => undefined;
  },
}));

import { BetaUpdatesSwitch } from "./AppSection";

const box = () => screen.getByLabelText("Beta updates") as HTMLInputElement;

beforeEach(() => {
  api.get_settings.mockReset();
  api.save_agent_settings.mockClear();
});
afterEach(() => cleanup());

describe("Beta updates switch", () => {
  it("shows the channel the backend reports", async () => {
    api.get_settings.mockResolvedValue({ update_channel: "beta" });
    render(<BetaUpdatesSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    expect(box().checked).toBe(true);
  });

  it("saves stable when switched off and beta when switched on", async () => {
    api.get_settings.mockResolvedValue({ update_channel: "beta" });
    render(<BetaUpdatesSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    await act(async () => {
      fireEvent.click(box());
    });
    expect(api.save_agent_settings).toHaveBeenLastCalledWith({ update_channel: "stable" });
    expect(box().checked).toBe(false);
    await act(async () => {
      fireEvent.click(box());
    });
    expect(api.save_agent_settings).toHaveBeenLastCalledWith({ update_channel: "beta" });
  });
});
