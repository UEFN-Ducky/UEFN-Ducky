// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const api = { live_overview: vi.fn() };
let ready = false;

vi.mock("./usePanelApi", () => ({ getApi: () => (ready ? api : null), isRemote: () => false }));

import { _resetLiveOverviewForTests, useLiveOverview } from "./useLiveOverview";
import { renderHook, waitFor } from "@testing-library/react";

afterEach(() => {
  _resetLiveOverviewForTests();
  ready = false;
  api.live_overview.mockReset();
  vi.useRealTimers();
});

describe("useLiveOverview", () => {
  it("reads the teams once the panel API is ready, not only after a plugin's scope push", async () => {
    api.live_overview.mockResolvedValue({ ok: true, on: false, teams: [{ space: "s1", label: "Mindful Path" }] });
    const { result } = renderHook(() => useLiveOverview());
    expect(api.live_overview).not.toHaveBeenCalled(); // no API yet: nothing to ask
    ready = true;
    window.dispatchEvent(new Event("pywebviewready"));
    await waitFor(() => expect(result.current?.teams?.[0]?.label).toBe("Mindful Path"));
    expect(api.live_overview).toHaveBeenCalledTimes(1);
  });
});
