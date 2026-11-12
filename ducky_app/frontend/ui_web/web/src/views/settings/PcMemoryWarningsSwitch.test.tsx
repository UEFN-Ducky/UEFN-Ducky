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

import { PcMemoryWarningsSwitch } from "./AddToUefnTab";

const box = () => screen.getByLabelText("PC memory warnings") as HTMLInputElement;

beforeEach(() => {
  api.get_settings.mockReset();
  api.save_agent_settings.mockClear();
});
afterEach(() => cleanup());

describe("PC memory warnings switch", () => {
  it("is on when the setting was never saved", async () => {
    api.get_settings.mockResolvedValue({});
    render(<PcMemoryWarningsSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    expect(box().checked).toBe(true);
  });

  it("saves off when switched off", async () => {
    api.get_settings.mockResolvedValue({ pc_memory_warnings: true });
    render(<PcMemoryWarningsSwitch />);
    await waitFor(() => expect(box().disabled).toBe(false));
    await act(async () => {
      fireEvent.click(box());
    });
    expect(api.save_agent_settings).toHaveBeenCalledWith({ pc_memory_warnings: false });
    expect(box().checked).toBe(false);
  });
});
