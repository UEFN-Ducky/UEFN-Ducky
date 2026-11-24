// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AgentEvent } from "../types/panel";

let handler: ((e: AgentEvent) => void) | null = null;
let remote = false;
const api = {
  remote_devices_list: vi.fn(async () => ({ ok: true, pending: [] as { id: string; label: string; code: string }[] })),
  remote_pair_decide: vi.fn(async (_id: string, _allow: boolean) => ({ ok: true })),
};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => remote }));
vi.mock("../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: (h: (e: AgentEvent) => void) => {
    handler = h;
    return () => {
      handler = null;
    };
  },
}));

import { RemotePairModal } from "./RemotePairModal";

beforeEach(() => {
  remote = false;
  api.remote_pair_decide.mockClear();
});
afterEach(() => cleanup());

async function ask() {
  render(<RemotePairModal />);
  await act(async () => {});
  await act(async () => {
    handler?.({ type: "remote_pair_request", id: "v1", label: "Pixel", code: "K7Q2MX" } as unknown as AgentEvent);
  });
}

it("shows the code with Deny focused, and Enter never approves", async () => {
  await ask();
  expect(screen.getByText("K7Q2MX")).toBeTruthy();
  const deny = screen.getByRole("button", { name: "Deny" });
  expect(document.activeElement).toBe(deny);
  const allow = screen.getByRole("button", { name: "Allow" });
  allow.focus();
  fireEvent.keyDown(allow, { key: "Enter" });
  expect(api.remote_pair_decide).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(allow);
  });
  expect(api.remote_pair_decide).toHaveBeenCalledWith("v1", true);
  expect(screen.queryByText("K7Q2MX")).toBeNull();
});

it("Deny and Escape deny", async () => {
  await ask();
  await act(async () => {
    fireEvent.keyDown(document, { key: "Escape" });
  });
  expect(api.remote_pair_decide).toHaveBeenCalledWith("v1", false);
});

it("a waiting request from before the window opened shows too", async () => {
  api.remote_devices_list.mockResolvedValueOnce({ ok: true, pending: [{ id: "v2", label: "Laptop", code: "ABCDEF" }] });
  render(<RemotePairModal />);
  await act(async () => {});
  expect(screen.getByText("ABCDEF")).toBeTruthy();
});

it("never shows in a remote view: the device asking can't approve itself", async () => {
  remote = true;
  await ask();
  expect(screen.queryByText("K7Q2MX")).toBeNull();
});
