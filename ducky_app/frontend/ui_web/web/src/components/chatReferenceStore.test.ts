// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { _resetChatReferenceStoreForTests, ensureLoaded, getChatReferenceSnapshot } from "./chatReferenceStore";
import { useChatReferenceCatalog } from "./useChatReferenceCatalog";
import type { PanelPushEvent } from "../types/panel";

const api = vi.hoisted(() => ({
  list_all_conversations: vi.fn(),
  list_agent_profiles: vi.fn(),
  get_skill_info: vi.fn(),
  list_mcp_servers: vi.fn(),
  list_recent_projects: vi.fn(),
  list_project_file_paths: vi.fn(),
  list_workflows: vi.fn(),
  list_plugin_references: vi.fn(),
}));
const push = vi.hoisted(() => ({ listener: null as ((event: PanelPushEvent) => void) | null }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/usePanelPushBus", () => ({
  subscribePanelPush: (fn: (event: PanelPushEvent) => void) => {
    push.listener = fn;
    return () => {
      push.listener = null;
    };
  },
}));
vi.mock("../hooks/usePluginContributions", () => {
  const value = { ui_panels: [], chat_references: [] };
  return { usePluginContributions: () => value };
});

const catalogCalls = () => Object.values(api).reduce((sum, fn) => sum + fn.mock.calls.length, 0);

beforeEach(() => {
  _resetChatReferenceStoreForTests();
  api.list_all_conversations.mockReset().mockResolvedValue([{ id: "c1", title: "Builder" }]);
  api.list_agent_profiles.mockReset().mockResolvedValue({ profiles: [] });
  api.get_skill_info.mockReset().mockResolvedValue({ packs: [] });
  api.list_mcp_servers.mockReset().mockResolvedValue({ plugins: [] });
  api.list_recent_projects.mockReset().mockResolvedValue([]);
  api.list_project_file_paths.mockReset().mockResolvedValue({ paths: ["Content/a.md"], total: 1, truncated: false });
  api.list_workflows.mockReset().mockResolvedValue({ workflows: [] });
  api.list_plugin_references.mockReset().mockResolvedValue({ refs: [] });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("switching 10 chats makes no catalog calls", () => {
  const { rerender } = renderHook(({ id }) => useChatReferenceCatalog(false, id), { initialProps: { id: "c0" } });
  for (let i = 1; i <= 10; i += 1) rerender({ id: `c${i}` });
  expect(catalogCalls()).toBe(0);
});

it("the first @ loads every list once; a second @ makes no calls", async () => {
  const { result } = renderHook(() => useChatReferenceCatalog(false, "c1"));
  act(() => ensureLoaded());
  expect(result.current.loading).toBe(true);
  await waitFor(() => expect(result.current.mentions.map((row) => row.label)).toEqual(["Builder"]));
  expect(result.current.files.map((row) => row.label)).toEqual(["a.md"]);
  expect(catalogCalls()).toBe(8);
  act(() => ensureLoaded());
  expect(catalogCalls()).toBe(8);
});

it("a plugin's live items (Kanban boards) show with its icon", async () => {
  api.list_plugin_references.mockResolvedValue({ refs: [
    { trigger: "/", id: "plugin:kanban/board:b1", label: "Limo backlog", group: "Kanban", href: "plugin:kanban/board:b1", icon_url: "data:image/svg+xml;base64,AA", plugin_id: "kanban" },
  ] });
  const { result } = renderHook(() => useChatReferenceCatalog(true, "c1"));
  await waitFor(() => expect(result.current.slashRefs.map((row) => row.label)).toContain("Limo backlog"));
  const board = result.current.slashRefs.find((row) => row.label === "Limo backlog");
  expect(board).toMatchObject({ group: "Kanban", href: "plugin:kanban/board:b1", iconUrl: "data:image/svg+xml;base64,AA" });
});

it("an @ opened 15 s after the last load reloads every list", async () => {
  ensureLoaded();
  await waitFor(() => expect(getChatReferenceSnapshot().status).toBe("ready"));
  const now = Date.now();
  const clock = vi.spyOn(Date, "now").mockReturnValue(now + 16_000);
  api.list_agent_profiles.mockResolvedValue({ profiles: [{ id: "p1", name: "New Ducky" }] });
  act(() => ensureLoaded());
  expect(getChatReferenceSnapshot().status).toBe("ready"); // the old rows stay up meanwhile
  await waitFor(() => expect(getChatReferenceSnapshot().data.profiles?.profiles?.[0]?.name).toBe("New Ducky"));
  clock.mockRestore();
});

it("duckies_changed refreshes the library duckies", async () => {
  ensureLoaded();
  await waitFor(() => expect(getChatReferenceSnapshot().status).toBe("ready"));
  vi.useFakeTimers();
  push.listener?.({ type: "duckies_changed" as PanelPushEvent["type"] });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(api.list_agent_profiles).toHaveBeenCalledTimes(2);
});

it("a push before the first load refreshes nothing", () => {
  vi.useFakeTimers();
  push.listener?.({ type: "uefn_plugins_changed" });
  vi.advanceTimersByTime(2000);
  expect(catalogCalls()).toBe(0);
});

it("chats_changed refreshes only the chat list, once, after 1 s — five pushes in 500 ms too", async () => {
  ensureLoaded();
  await waitFor(() => expect(getChatReferenceSnapshot().status).toBe("ready"));
  vi.useFakeTimers();
  api.list_all_conversations.mockClear();
  const before = catalogCalls();
  for (let i = 0; i < 5; i += 1) {
    push.listener?.({ type: "chats_changed" as PanelPushEvent["type"] });
    vi.advanceTimersByTime(100);
  }
  expect(api.list_all_conversations).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(600);
  });
  expect(api.list_all_conversations).toHaveBeenCalledOnce();
  expect(catalogCalls() - before).toBe(1);
});

it("project_changed refreshes the recent projects and the file list", async () => {
  ensureLoaded();
  await waitFor(() => expect(getChatReferenceSnapshot().status).toBe("ready"));
  vi.useFakeTimers();
  api.list_project_file_paths.mockResolvedValue({ paths: ["notes/b.md"], total: 1, truncated: false });
  push.listener?.({ type: "project_changed" });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(api.list_recent_projects).toHaveBeenCalledTimes(2);
  expect(api.list_project_file_paths).toHaveBeenCalledTimes(2);
  expect(api.list_all_conversations).toHaveBeenCalledTimes(1);
  expect(getChatReferenceSnapshot().data.paths).toEqual({ paths: ["notes/b.md"], total: 1, truncated: false });
});
