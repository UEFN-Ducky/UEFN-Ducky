// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { duckiesLayoutHold } from "../utils/duckiesLayoutHold";
import { useChatFolders } from "./useChatFolders";

vi.mock("../utils/duckiesTreePrefs", () => ({ readDuckiesAllProjects: () => false }));

afterEach(() => {
  cleanup();
  delete window.pywebview;
});

const conv = (id: string, extra: Record<string, unknown> = {}) => ({ id, title: id, ...extra });

function install(api: Record<string, unknown>) {
  window.pywebview = { api: { get_listener_status: vi.fn(), ...api } } as unknown as typeof window.pywebview;
}

it("puts no-island duckies, groups and folders in the Global Agents folder on any island", async () => {
  install({
    list_folders: vi.fn().mockResolvedValue([
      { id: "squad", name: "Squad", parent_id: "", sort_order: 0, group_hub_id: "hub" },
      { id: "ga-group", name: "Crew", parent_id: "", sort_order: 0, group_hub_id: "ga-hub", project_slug: "_no_project" },
      { id: "ga-sub", name: "Notes", parent_id: "ga-group", sort_order: 0, project_slug: "_no_project" },
    ]),
    list_all_conversations: vi.fn().mockImplementation(async (all?: boolean) => all ? [] : [
      conv("c1"),
      conv("hub", { folder_id: "squad", is_group: true }),
      conv("m1", { folder_id: "squad" }),
      conv("ga-loose", { project_slug: "_no_project" }),
      conv("ga-hub", { folder_id: "ga-group", is_group: true, project_slug: "_no_project" }),
      conv("ga-member", { folder_id: "ga-group", project_slug: "_no_project" }),
    ]),
  });
  const { result } = renderHook(() => useChatFolders(0, "here"));
  await waitFor(() => expect(result.current.foldersLoaded).toBe(true));
  expect(result.current.rootChats.map((c) => c.id)).toEqual(["c1"]);
  expect(result.current.folders.map((f) => f.id)).toEqual(["squad", "project:_no_project"]);
  const global = result.current.folders[1];
  expect(global.chats.map((c) => c.id)).toEqual(["ga-loose"]);
  expect(global.children.map((f) => f.id)).toEqual(["ga-group"]);
  expect(global.children[0].chats.map((c) => c.id)).toEqual(["ga-member"]);
  expect(global.children[0].children.map((f) => f.id)).toEqual(["ga-sub"]);
  expect(global.children[0].projectSlug).toBe("_no_project");
  // Group hubs stay findable for their chat tabs.
  expect(result.current.hubChats.map((c) => c.id).sort()).toEqual(["ga-hub", "hub"]);
});

it("drops deleted rows at once, and a slower older answer never brings them back", async () => {
  let release: (rows: unknown[]) => void = () => {};
  const list = vi.fn()
    .mockImplementationOnce(async () => [conv("c1"), conv("c2")])
    .mockImplementationOnce(async () => [])
    .mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
    .mockImplementation(async () => []);
  install({ list_folders: vi.fn().mockResolvedValue([]), list_all_conversations: list });
  const { result } = renderHook(() => useChatFolders(0, "here"));
  await waitFor(() => expect(result.current.rootChats.map((c) => c.id)).toEqual(["c1", "c2"]));
  // A reload starts (it will answer late, with c2 still there)…
  let slow: Promise<void> = Promise.resolve();
  act(() => { slow = result.current.load(); });
  // …then another window deletes c2.
  act(() => result.current.removeRows(["c2"], []));
  expect(result.current.rootChats.map((c) => c.id)).toEqual(["c1"]);
  await act(async () => {
    release([conv("c1"), conv("c2")]);
    await slow;
  });
  expect(result.current.rootChats.map((c) => c.id)).toEqual(["c1"]);
});

it("never paints a reload over a move that is still saving", async () => {
  const rows = [conv("c1"), conv("c2")];
  install({
    list_folders: vi.fn().mockResolvedValue([]),
    list_all_conversations: vi.fn().mockImplementation(async (all?: boolean) => (all ? [] : rows)),
  });
  const { result } = renderHook(() => useChatFolders(0, "here"));
  await waitFor(() => expect(result.current.rootChats.map((c) => c.id)).toEqual(["c1", "c2"]));
  const releaseHold = duckiesLayoutHold.hold();
  act(() => result.current.setRootChats([{ id: "c2", name: "c2" }, { id: "c1", name: "c1" }]));
  await act(async () => { await result.current.load(); });
  expect(result.current.rootChats.map((c) => c.id)).toEqual(["c2", "c1"]); // the moved order stays
  // The save finished: the host's order (now c2, c1) is loaded once.
  rows.reverse();
  await act(async () => { releaseHold(); });
  await waitFor(() => expect(result.current.rootChats.map((c) => c.id)).toEqual(["c2", "c1"]));
});
