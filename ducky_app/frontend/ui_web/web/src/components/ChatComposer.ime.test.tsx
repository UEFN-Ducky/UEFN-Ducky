// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { composerHistoryKey, primaryMod } from "../hooks/composerHistory";
import { useUndoShortcuts } from "../navigation/useUndoShortcuts";
import { isImeKey } from "./ChatComposer";

function setPlatform(platform: string) {
  Object.defineProperty(navigator, "platform", { value: platform, configurable: true });
}

afterEach(() => setPlatform(""));

describe("input methods: Enter while composing never sends", () => {
  it("treats a composing Enter and WebKit's keyCode 229 commit as the IME's", () => {
    expect(isImeKey({ keyCode: 13, nativeEvent: { isComposing: true } })).toBe(true);
    expect(isImeKey({ keyCode: 229, nativeEvent: { isComposing: false } })).toBe(true);
    expect(isImeKey({ keyCode: 13, nativeEvent: { isComposing: false } })).toBe(false);
  });
});

describe("Linux shortcut map has no Super binding", () => {
  const key = (k: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }> = {}) =>
    ({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });

  it("Super+Z / Super+Y do nothing on Linux, Ctrl still works", () => {
    setPlatform("Linux x86_64");
    expect(primaryMod({ ctrlKey: false, metaKey: true })).toBe(false);
    expect(composerHistoryKey(key("z", { metaKey: true }))).toBeNull();
    expect(composerHistoryKey(key("y", { metaKey: true }))).toBeNull();
    expect(composerHistoryKey(key("z", { ctrlKey: true }))).toBe("undo");
  });

  it("Windows and macOS keep Win/Cmd as today", () => {
    setPlatform("Win32");
    expect(composerHistoryKey(key("z", { metaKey: true }))).toBe("undo");
    setPlatform("MacIntel");
    expect(composerHistoryKey(key("y", { metaKey: true }))).toBe("redo");
  });

  it("tree undo ignores Super+Z on Linux", () => {
    setPlatform("Linux x86_64");
    const host = document.createElement("div");
    host.setAttribute("data-undo-scope", "content");
    host.tabIndex = 0;
    document.body.appendChild(host);
    host.focus();
    const undo = vi.fn();
    renderHook(() => useUndoShortcuts(undo, () => {}));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "z", metaKey: true }));
    expect(undo).not.toHaveBeenCalled();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "z", ctrlKey: true }));
    expect(undo).toHaveBeenCalledWith("content");
    host.remove();
  });
});
