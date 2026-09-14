import { describe, expect, it } from "vitest";
import type { EditorTab } from "../../types/panel";
import {
  clearQueries,
  DEFAULT_DESTINATIONS,
  mergeIdleResources,
  mergeIdleServices,
  pushQuery,
  pushResource,
  pushService,
  readQueries,
  readResources,
  readServices,
  RECENTS_CAP,
  resourceTypeLabel,
  type RecentsStore,
} from "./quickOpenRecents";

function memStore(): RecentsStore {
  const map = new Map<string, string>();
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => {
      map.set(key, value);
    },
    removeItem: (key) => {
      map.delete(key);
    },
  };
}

describe("quick-open recents", () => {
  it("skips empty queries and caps at 12, newest first", () => {
    const store = memStore();
    expect(pushQuery("  ", store)).toEqual([]);
    pushQuery("alpha", store);
    pushQuery("beta", store);
    pushQuery("alpha", store);
    expect(readQueries(store)).toEqual(["alpha", "beta"]);
    for (let i = 0; i < 20; i++) pushQuery(`q${i}`, store);
    const queries = readQueries(store);
    expect(queries).toHaveLength(RECENTS_CAP);
    expect(queries[0]).toBe("q19");
  });

  it("clears search history", () => {
    const store = memStore();
    pushQuery("keep", store);
    clearQueries(store);
    expect(readQueries(store)).toEqual([]);
  });

  it("records services and resources without duplicates", () => {
    const store = memStore();
    pushService("settings:Store", store);
    pushService("chat:abc", store);
    pushService("settings:Store", store);
    expect(readServices(store)).toEqual(["settings:Store", "chat:abc"]);

    pushResource({ id: "file:a.verse", kind: "file", label: "a.verse", path: "a.verse" }, store);
    pushResource({ id: "chat:1", kind: "chat", label: "Helper", chatId: "1" }, store);
    pushResource({ id: "file:a.verse", kind: "file", label: "a.verse", path: "a.verse" }, store);
    expect(readResources(store).map((r) => r.id)).toEqual(["file:a.verse", "chat:1"]);
  });

  it("fills idle services with recent agents then default destinations", () => {
    const chats = [
      { id: "c1", name: "Certified Nursing Assistant" },
      { id: "c2", name: "Shop" },
    ];
    const tabs: Pick<EditorTab, "kind" | "chatId" | "name">[] = [
      { kind: "chat", chatId: "c2", name: "Shop" },
    ];
    const merged = mergeIdleServices(["chat:c1", "settings:LLMs"], chats, tabs, 8);
    expect(merged[0]).toMatchObject({ id: "chat:c1", label: "Certified Nursing Assistant", kind: "chat" });
    expect(merged.map((s) => s.id)).toContain("settings:LLMs");
    expect(merged.map((s) => s.id)).toContain("chat:c2");
    expect(merged.map((s) => s.id)).toContain("settings:Store");
    expect(merged).toHaveLength(8);
    expect(DEFAULT_DESTINATIONS.length).toBe(8);
  });

  it("merges stored resources ahead of open tabs", () => {
    const stored = [{ id: "chat:old", kind: "chat" as const, label: "Old", chatId: "old" }];
    const tabs: EditorTab[] = [
      { id: "file:x", kind: "file", name: "x.verse", path: "Content/x.verse" },
      { id: "chat:old", kind: "chat", name: "Old", chatId: "old" },
    ];
    const merged = mergeIdleResources(stored, tabs);
    expect(merged[0].id).toBe("chat:old");
    expect(merged.some((r) => r.kind === "file" && r.path === "Content/x.verse")).toBe(true);
    expect(resourceTypeLabel("chat")).toBe("Ducky");
    expect(resourceTypeLabel("file")).toBe("File");
  });
});
