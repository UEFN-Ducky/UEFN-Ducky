// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatTab, EditorTab } from "../types/panel";
import { createDefaultLayout, createEmptyLayout } from "../utils/editorLayoutOps";

const api = {
  save_editor_workspace: vi.fn(async () => {}),
  get_editor_workspace: vi.fn(),
  stat_project_file: vi.fn(async (path: string) => ({ path, mtime_ns: 1, size: 1, exists: true })),
  read_project_file: vi.fn(),
  close_all_focus_windows: vi.fn(async () => {}),
  restore_focus_windows: vi.fn(async () => {}),
};
vi.mock("./usePanelApi", () => ({ getApi: () => api }));

const { useEditorWorkspace } = await import("./useEditorWorkspace");

const chat: ChatTab = { id: "c1", name: "Ducky" } as ChatTab;
const savedTabs: EditorTab[] = [
  { id: "chat:c1", kind: "chat", chatId: "c1", name: "Ducky" },
  { id: "file:a.verse", kind: "file", path: "a.verse", name: "a.verse" },
];

function render(slug: string, foldersLoaded: boolean) {
  const initLayoutState = vi.fn();
  const hook = renderHook(
    (props: { foldersLoaded: boolean; openTabs: EditorTab[] }) =>
      useEditorWorkspace({
        projectSlug: slug,
        allChats: [chat],
        foldersLoaded: props.foldersLoaded,
        openTabs: props.openTabs,
        layout: props.openTabs.length ? createDefaultLayout(props.openTabs.map((t) => t.id)) : createEmptyLayout(),
        initLayoutState,
      }),
    { initialProps: { foldersLoaded, openTabs: [] as EditorTab[] } },
  );
  return { ...hook, initLayoutState };
}

beforeEach(() => {
  vi.useFakeTimers();
  api.save_editor_workspace.mockClear();
  api.get_editor_workspace.mockReset().mockResolvedValue({
    version: 1,
    openTabs: savedTabs,
    layout: createDefaultLayout(savedTabs.map((t) => t.id)),
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("never saves the empty pre-restore state, and saves once the snapshot is applied", async () => {
  const slug = `slow-folders-${Math.random()}`;
  const { rerender, initLayoutState } = render(slug, false);
  // The conversation list is still loading 300ms after mount: the debounced save
  // must not overwrite the stored workspace with the empty initial state.
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(api.save_editor_workspace).not.toHaveBeenCalled();

  rerender({ foldersLoaded: true, openTabs: [] });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(initLayoutState).toHaveBeenCalledWith(
    expect.arrayContaining([expect.objectContaining({ id: "chat:c1" }), expect.objectContaining({ id: "file:a.verse" })]),
    expect.anything(),
  );
  // Existence is checked with a stat, not a full content read.
  expect(api.stat_project_file).toHaveBeenCalledWith("a.verse");
  expect(api.read_project_file).not.toHaveBeenCalled();

  rerender({ foldersLoaded: true, openTabs: savedTabs });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(api.save_editor_workspace).toHaveBeenCalledTimes(1);
  expect(api.save_editor_workspace.mock.calls[0]![0].openTabs).toHaveLength(2);
});

it("retries a restore that was cancelled before the snapshot was applied", async () => {
  const slug = `flap-${Math.random()}`;
  let release!: (v: unknown) => void;
  api.get_editor_workspace.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
  const { rerender, initLayoutState } = render(slug, true);
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  // Sidebar refresh flaps foldersLoaded while the snapshot is still being read.
  rerender({ foldersLoaded: false, openTabs: [] });
  release({ version: 1, openTabs: savedTabs, layout: createDefaultLayout(savedTabs.map((t) => t.id)) });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(initLayoutState).not.toHaveBeenCalled();

  rerender({ foldersLoaded: true, openTabs: [] });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(initLayoutState).toHaveBeenCalledTimes(1);
});
