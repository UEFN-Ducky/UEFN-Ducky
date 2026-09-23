// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ProjectFileListing } from "../types/panel";
import { useWorkspaceTreeData } from "./useWorkspaceTreeData";

const api = vi.hoisted(() => ({ list_project_files: vi.fn(), list_workspace_roots: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));
vi.mock("./onApiReady", () => ({ onApiReady: (fn: () => void) => { fn(); return () => {}; } }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const roots: ProjectFileListing = { path: "__roots__", entries: [
  { name: "Island", path: "ws:0", is_dir: true, read_only: false, kind: "content" },
  { name: "Verse", path: "ws:1", is_dir: true, read_only: true, kind: "core" },
] };
const content = (name: string): ProjectFileListing => ({ path: "Content", entries: [{ name, path: `Content/${name}`, is_dir: false }] });
beforeEach(() => {
  api.list_project_files.mockReset().mockImplementation(async (path: string) => path === "__roots__" ? roots : content("new.verse"));
  api.list_workspace_roots.mockReset().mockResolvedValue([]);
});
afterEach(cleanup);

function render(slug = "A", expanded = new Set<string>()) {
  const expandedRef = { current: expanded };
  return renderHook(({ project }) => useWorkspaceTreeData(project, 0, true, expandedRef), { initialProps: { project: slug } });
}

it("shows Content while optional core and workspace metadata are still loading", async () => {
  const metadata = deferred<[]>();
  api.list_workspace_roots.mockReturnValue(metadata.promise);
  const { result } = render();
  await act(async () => {});
  expect(result.current.loadingRoot).toBe(false);
  expect(result.current.rootEntries[0]?.path).toBe("Content");
  expect(result.current.cache.get("Content")?.[0]?.name).toBe("new.verse");
  const core = deferred<ProjectFileListing>();
  api.list_project_files.mockImplementation(async (path: string) => path === "ws:1" ? core.promise : roots);
  await act(async () => { metadata.resolve([]); });
  expect(result.current.cache.get("ws:0")?.[0]?.name).toBe("new.verse");
  expect(result.current.loadingPaths.has("ws:1")).toBe(true);
  expect(result.current.loadingRoot).toBe(false);
  await act(async () => { core.resolve({ path: "ws:1", entries: [] }); });
});

it("deduplicates overlapping refreshes and directory reads", async () => {
  const pending = deferred<ProjectFileListing>();
  api.list_project_files.mockImplementation((path: string) => path === "Content" ? pending.promise : Promise.resolve(roots));
  const { result } = render();
  act(() => {
    void result.current.reloadTree();
    void result.current.reloadTree();
    void result.current.loadDir("Content");
  });
  expect(api.list_project_files.mock.calls.filter(([path]) => path === "Content")).toHaveLength(1);
  expect(api.list_project_files.mock.calls.filter(([path]) => path === "__roots__")).toHaveLength(1);
  await act(async () => { pending.resolve(content("a.verse")); });
});

it("does not wait for the old project or publish its late response after a switch", async () => {
  const old = deferred<ProjectFileListing>();
  api.list_project_files.mockImplementation((path: string) => path === "Content" ? old.promise : Promise.resolve(roots));
  const { result, rerender } = render();
  await act(async () => {});
  api.list_project_files.mockImplementation(async (path: string) => path === "__roots__" ? roots : content("B.verse"));
  rerender({ project: "B" });
  await act(async () => {});
  expect(result.current.loadingRoot).toBe(false);
  expect(result.current.cache.get("ws:0")?.[0]?.name).toBe("B.verse");
  await act(async () => { old.resolve(content("A.verse")); });
  expect(result.current.cache.get("ws:0")?.[0]?.name).toBe("B.verse");
});

it("bounds restored-folder requests and stops scheduling the old project on unmount", async () => {
  const pending = deferred<ProjectFileListing>();
  api.list_project_files.mockImplementation((path: string) => {
    if (path === "Content") return Promise.resolve(content("a.verse"));
    if (path === "__roots__") return Promise.resolve(roots);
    return pending.promise;
  });
  const { unmount } = render("A", new Set(Array.from({ length: 30 }, (_, i) => `Content/Folder${i}`)));
  await act(async () => {});
  expect(api.list_project_files).toHaveBeenCalledTimes(6); // Content, roots, four workers
  unmount();
  await act(async () => { pending.resolve({ path: "", entries: [] }); });
  expect(api.list_project_files).toHaveBeenCalledTimes(6);
});

it("keeps Content usable when optional metadata fails", async () => {
  api.list_workspace_roots.mockRejectedValue(new Error("Unavailable"));
  const { result } = render();
  await act(async () => {});
  expect(result.current.loadingRoot).toBe(false);
  expect(result.current.cache.get("Content")?.[0]?.name).toBe("new.verse");
  expect(result.current.error).toBeNull();
});

it("rechecks a mutation that arrives during an existing refresh", async () => {
  const pending = deferred<ProjectFileListing>();
  api.list_project_files.mockImplementation((path: string) => path === "Content" ? pending.promise : Promise.resolve(roots));
  const { result } = render();
  act(() => { void result.current.reloadTree(true); });
  api.list_project_files.mockImplementation(async (path: string) => path === "__roots__" ? roots : content("after-mutation.verse"));
  await act(async () => { pending.resolve(content("before-mutation.verse")); });
  expect(result.current.cache.get("ws:0")?.[0]?.name).toBe("after-mutation.verse");
  expect(api.list_project_files.mock.calls.filter(([path]) => path === "Content")).toHaveLength(2);
});
