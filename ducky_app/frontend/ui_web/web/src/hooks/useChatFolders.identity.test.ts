// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  folders: [{ id: "f1", name: "Team", parent_id: "", sort_order: 0 }] as Record<string, unknown>[],
  convs: [] as Record<string, unknown>[],
}));
vi.mock("./usePanelApi", () => ({
  getApi: () => ({
    list_folders: async () => host.folders.map((f) => ({ ...f })),
    list_all_conversations: async () => host.convs.map((c) => ({ ...c })),
  }),
}));
vi.mock("./onApiReady", () => ({ onApiReady: (fn: () => void) => { fn(); return () => {}; } }));

import { shareRows, useChatFolders } from "./useChatFolders";

const conv = (id: string, title: string, folder = "") => ({ id, title, folder_id: folder, updated: 1 });

it("a reload with one renamed row keeps every other row object", async () => {
  host.convs = [conv("a", "A"), conv("b", "B"), conv("c", "C", "f1"), conv("d", "D", "f1")];
  const { result } = renderHook(() => useChatFolders(0));
  await act(async () => { await result.current.load(); });
  const root1 = result.current.rootChats;
  const folders1 = result.current.folders;
  const [a1, b1] = root1;
  const [c1, d1] = folders1[0].chats;

  host.convs = [conv("a", "A renamed"), conv("b", "B"), conv("c", "C", "f1"), conv("d", "D", "f1")];
  await act(async () => { await result.current.load(); });
  const [a2, b2] = result.current.rootChats;
  expect(a2).not.toBe(a1);
  expect(a2.name).toBe("A renamed");
  expect(b2).toBe(b1);
  // The folder holding no change is the same object, rows and all.
  expect(result.current.folders).toBe(folders1);
  expect(result.current.folders[0].chats[0]).toBe(c1);
  expect(result.current.folders[0].chats[1]).toBe(d1);

  // Nothing changed: the lists themselves are kept.
  const root2 = result.current.rootChats;
  await act(async () => { await result.current.load(); });
  expect(result.current.rootChats).toBe(root2);
});

it("shareRows matches array items by id, so a move keeps the moved row", () => {
  const x = { id: "x", n: 1 };
  const y = { id: "y", n: 2 };
  const out = shareRows([x, y], [{ id: "y", n: 2 }, { id: "x", n: 1 }]);
  expect(out[0]).toBe(y);
  expect(out[1]).toBe(x);
  expect(shareRows([x, y], [{ id: "x", n: 1 }, { id: "y", n: 2 }])).toEqual([x, y]);
});
