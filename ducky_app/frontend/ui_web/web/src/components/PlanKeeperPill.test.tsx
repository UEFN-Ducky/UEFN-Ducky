// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ChatPlan } from "../types/panel";
const api = vi.hoisted(() => ({
  team_keeper_status: vi.fn(async (_chat: string) => ({ state: "watching" as string })),
  update_plan: vi.fn(async (..._args: unknown[]) => ({ ok: true })),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
import { PlanKeeperPill } from "./PlanKeeperPill";

const plan: ChatPlan = {
  id: "plan", chat_id: "lead", title: "Ship", status: "open", todos: [],
  nodes: [{ id: "a", content: "Step A", status: "in_progress", children: [] }],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("shows the keeper on a kept plan and pauses the plan", async () => {
  render(<PlanKeeperPill plan={plan} />);
  const pill = await screen.findByText("Keeper on · Pause");
  expect(api.team_keeper_status).toHaveBeenCalledWith("lead");
  fireEvent.click(pill);
  await waitFor(() => expect(api.update_plan).toHaveBeenCalledWith("lead", undefined, "", "", "", true, "paused", null));
});

it("a paused plan offers Resume and asks the keeper nothing", async () => {
  render(<PlanKeeperPill plan={{ ...plan, status: "paused" }} />);
  fireEvent.click(screen.getByText("Paused · Resume"));
  await waitFor(() => expect(api.update_plan).toHaveBeenCalledWith("lead", undefined, "", "", "", true, "open", null));
  expect(api.team_keeper_status).not.toHaveBeenCalled();
});

it("stays hidden when the keeper does not keep the plan", async () => {
  api.team_keeper_status.mockResolvedValueOnce({ state: "off" });
  const { container } = render(<PlanKeeperPill plan={plan} />);
  await waitFor(() => expect(api.team_keeper_status).toHaveBeenCalled());
  expect(container.textContent).toBe("");
});
