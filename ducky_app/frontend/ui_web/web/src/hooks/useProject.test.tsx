// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectInfo } from "../types/panel";
import { PROJECT_SELECTED_EVENT, useProject } from "./useProject";

const api = vi.hoisted(() => ({ get_project_info: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./onApiReady", () => ({ onApiReady: (fn: (value: typeof api) => void) => { fn(api); return () => {}; } }));
afterEach(cleanup);

it("applies a completed selection immediately and ignores an older poll", async () => {
  let release!: (info: ProjectInfo) => void;
  api.get_project_info.mockReturnValue(new Promise<ProjectInfo>((resolve) => { release = resolve; }));
  const { result } = renderHook(() => useProject());
  const selected = { path: "C:/B", name: "B", slug: "B" };
  act(() => window.dispatchEvent(new CustomEvent(PROJECT_SELECTED_EVENT, { detail: selected })));
  expect(result.current).toEqual(selected);
  await act(async () => { release({ path: "C:/A", name: "A", slug: "A" }); });
  expect(result.current).toEqual(selected);
});

it("does not re-render when the poll returns the same project", async () => {
  vi.useFakeTimers();
  try {
    api.get_project_info.mockImplementation(() =>
      Promise.resolve({ path: "C:/Island", name: "Island", slug: "island", kind: "island", content_root: "Content" }),
    );
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useProject(15000);
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.name).toBe("Island");
    const first = result.current;
    const rendersAfterFirst = renders;

    for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(15000); });

    expect(api.get_project_info.mock.calls.length).toBeGreaterThanOrEqual(10);
    expect(renders).toBe(rendersAfterFirst);
    expect(result.current).toBe(first);
  } finally {
    vi.useRealTimers();
    api.get_project_info.mockReset();
  }
});

it("does not poll for the project while the window is hidden", async () => {
  vi.useFakeTimers();
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  try {
    api.get_project_info.mockResolvedValue({ path: "", name: "No project", slug: "_no_project" });
    renderHook(() => useProject(15000));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const callsAtStart = api.get_project_info.mock.calls.length;

    await act(async () => { await vi.advanceTimersByTimeAsync(15000 * 4); });

    expect(api.get_project_info.mock.calls.length).toBe(callsAtStart);
  } finally {
    hidden.mockRestore();
    vi.useRealTimers();
    api.get_project_info.mockReset();
  }
});
