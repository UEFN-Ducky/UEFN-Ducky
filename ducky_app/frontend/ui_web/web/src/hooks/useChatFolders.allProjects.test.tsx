// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useChatFolders } from "./useChatFolders";

vi.mock("../utils/duckiesTreePrefs", () => ({ readDuckiesAllProjects: () => true }));

afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("keeps a recent project that has no duckies", async () => {
  window.pywebview = { api: {} } as typeof window.pywebview;
  const { result } = renderHook(() => useChatFolders(0, "here"));
  await act(async () => {
    window.pywebview = {
      api: {
        get_listener_status: vi.fn(),
        list_folders: vi.fn().mockResolvedValue([]),
        list_all_conversations: vi.fn().mockResolvedValue([]),
        list_recent_projects: vi.fn().mockResolvedValue([
          { slug: "empty_island", name: "Empty", path: "C:/islands/Empty", active: false },
          { slug: "here", name: "Here", path: "C:/islands/Here", active: true },
        ]),
      },
    } as unknown as typeof window.pywebview;
    window.dispatchEvent(new Event("pywebviewready"));
  });
  await waitFor(() => expect(result.current.foldersLoaded).toBe(true));
  // Global Agents is always there, even empty, so duckies can be dropped into it.
  expect(result.current.folders.map((folder) => folder.id)).toEqual(["project:here", "project:_no_project", "project:empty_island"]);
  expect(result.current.folders.every((folder) => folder.chats.length === 0 && folder.children.length === 0)).toBe(true);
});
