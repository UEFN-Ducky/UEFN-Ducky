import { describe, expect, it } from "vitest";

import {
  autoTranslateAllFilesFromPrefs,
  autoTranslateChatsFromPrefs,
  autoTranslateFilesFromPrefs,
  canVisualTranslateFile,
  isAutoTranslateChat,
  isAutoTranslateFile,
  serializeIdList,
  toggleAutoTranslateChat,
  toggleAutoTranslateFile,
  toggleInList,
} from "./tabTranslatePrefs";

describe("tabTranslatePrefs", () => {
  it("toggles paths case-insensitively", () => {
    const next = toggleInList(["Content/A.verse"], "content/a.verse");
    expect(next).toEqual([]);
    expect(toggleInList([], "Content/B.verse")).toEqual(["Content/B.verse"]);
  });

  it("allowlist when global auto is off", () => {
    const prefs = {
      autoTranslateFiles: serializeIdList(["Verse/x.verse"]),
      autoTranslateChats: serializeIdList(["chat-1"]),
    };
    expect(autoTranslateFilesFromPrefs(prefs)).toEqual(["Verse/x.verse"]);
    expect(autoTranslateChatsFromPrefs(prefs)).toEqual(["chat-1"]);
    expect(isAutoTranslateFile("verse/x.verse", prefs)).toBe(true);
    expect(isAutoTranslateFile("verse/y.verse", prefs)).toBe(false);
    expect(isAutoTranslateChat("chat-1", prefs)).toBe(true);
    expect(isAutoTranslateChat("chat-2", prefs)).toBe(false);
  });

  it("global auto-files with per-file opt-out", () => {
    const prefs = {
      autoTranslateAllFiles: true,
      autoTranslateFilesOff: serializeIdList(["Verse/skip.verse"]),
    };
    expect(autoTranslateAllFilesFromPrefs(prefs)).toBe(true);
    expect(isAutoTranslateFile("Verse/any.verse", prefs)).toBe(true);
    expect(isAutoTranslateFile("Verse/skip.verse", prefs)).toBe(false);
  });

  it("toggleAutoTranslateFile flips allowlist / denylist", () => {
    const writes: Record<string, unknown> = {
      autoTranslateAllFiles: false,
      autoTranslateFiles: serializeIdList([]),
    };
    const setPref = (k: string, v: unknown) => {
      writes[k] = v;
    };
    expect(toggleAutoTranslateFile("Verse/a.verse", writes, setPref)).toBe(true);
    expect(isAutoTranslateFile("Verse/a.verse", writes)).toBe(true);
    expect(toggleAutoTranslateFile("Verse/a.verse", writes, setPref)).toBe(false);
    expect(isAutoTranslateFile("Verse/a.verse", writes)).toBe(false);

    writes.autoTranslateAllFiles = true;
    writes.autoTranslateFilesOff = serializeIdList([]);
    expect(isAutoTranslateFile("Verse/b.verse", writes)).toBe(true);
    expect(toggleAutoTranslateFile("Verse/b.verse", writes, setPref)).toBe(false);
    expect(isAutoTranslateFile("Verse/b.verse", writes)).toBe(false);
    expect(toggleAutoTranslateFile("Verse/b.verse", writes, setPref)).toBe(true);
    expect(isAutoTranslateFile("Verse/b.verse", writes)).toBe(true);
  });

  it("canVisualTranslateFile allows text/ext and rejects binaries", () => {
    expect(canVisualTranslateFile("Content/Foo.verse")).toBe(true);
    expect(canVisualTranslateFile("ext:c:/tmp/notes.md")).toBe(true);
    expect(canVisualTranslateFile("Content/Art/icon.png")).toBe(false);
    expect(canVisualTranslateFile("Content/Map.umap")).toBe(false);
  });

  it("toggleAutoTranslateChat with global on", () => {
    const writes: Record<string, unknown> = {
      autoTranslateAllChats: true,
      autoTranslateChatsOff: serializeIdList([]),
    };
    const setPref = (k: string, v: unknown) => {
      writes[k] = v;
    };
    expect(isAutoTranslateChat("c1", writes)).toBe(true);
    expect(toggleAutoTranslateChat("c1", writes, setPref)).toBe(false);
    expect(isAutoTranslateChat("c1", writes)).toBe(false);
  });
});
