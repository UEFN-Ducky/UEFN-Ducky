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
