// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useChatFolders } from "./useChatFolders";

vi.mock("../utils/duckiesTreePrefs", () => ({ readDuckiesAllProjects: () => false }));

afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("waits for the native API and loads saved duckies when it becomes ready", async () => {
  window.pywebview = { api: {} } as typeof window.pywebview;
  const { result } = renderHook(() => useChatFolders(0));
  expect(result.current.foldersLoaded).toBe(false);
  const list = vi.fn().mockResolvedValue([{ id: "saved", title: "My saved ducky" }]);
  await act(async () => {
    window.pywebview = { api: {
      get_listener_status: vi.fn(), list_folders: vi.fn().mockResolvedValue([]),
      list_all_conversations: list,
    } } as unknown as typeof window.pywebview;
    window.dispatchEvent(new Event("pywebviewready"));
  });
  await waitFor(() => expect(result.current.foldersLoaded).toBe(true));
  expect(result.current.rootChats.map((chat) => chat.id)).toEqual(["saved"]);
  expect(list).toHaveBeenCalledOnce();
});

it("cancels the readiness subscription when the sidebar unmounts", async () => {
  window.pywebview = { api: {} } as typeof window.pywebview;
  const { unmount } = renderHook(() => useChatFolders(0));
  unmount();
  const list = vi.fn().mockResolvedValue([]);
  await act(async () => {
    window.pywebview = { api: {
      get_listener_status: vi.fn(), list_folders: list, list_all_conversations: list,
    } } as unknown as typeof window.pywebview;
    window.dispatchEvent(new Event("pywebviewready"));
  });
  expect(list).not.toHaveBeenCalled();
});
