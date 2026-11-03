// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ plugin_team_overview: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("../hooks/onApiReady", () => ({ onApiReady: () => () => {} }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));

import { _resetActiveTeamForTests, readActiveTeam, useActiveTeam } from "./activeTeamStore";
import { renderHook, act } from "@testing-library/react";

const overview = { ok: true, current: "team-a", label: "Team A", plugins: [], etag: "v1" };

afterEach(() => {
  _resetActiveTeamForTests();
  api.plugin_team_overview.mockReset();
});

it("passes its etag back and keeps the team on unchanged", async () => {
  api.plugin_team_overview.mockResolvedValueOnce(overview);
  const { result } = renderHook(() => useActiveTeam());
  await act(() => readActiveTeam());
  const first = result.current;
  expect(first?.label).toBe("Team A");
  expect(first).not.toHaveProperty("etag");
  expect(api.plugin_team_overview).toHaveBeenLastCalledWith("", "");

  api.plugin_team_overview.mockResolvedValueOnce({ ok: true, unchanged: true, etag: "v1" });
  await act(() => readActiveTeam());
  expect(api.plugin_team_overview).toHaveBeenLastCalledWith("", "v1");
  expect(result.current).toBe(first); // same object: nothing redraws

  api.plugin_team_overview.mockResolvedValueOnce({ ...overview, label: "Team B", etag: "v2" });
  await act(() => readActiveTeam());
  expect(result.current?.label).toBe("Team B");
  api.plugin_team_overview.mockResolvedValueOnce({ ok: true, unchanged: true, etag: "v2" });
  await act(() => readActiveTeam());
  expect(api.plugin_team_overview).toHaveBeenLastCalledWith("", "v2");
});
