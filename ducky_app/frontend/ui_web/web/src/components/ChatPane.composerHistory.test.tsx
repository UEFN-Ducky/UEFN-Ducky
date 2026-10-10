// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Every bridge call answers empty (lists and loads [], everything else {}): the pane renders offline.
const api = vi.hoisted(() => new Proxy({} as Record<string, unknown>, {
  get: (_t, key) => (key === "then" ? undefined : async () => (/^(list|load)_/.test(String(key)) ? [] : {})),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, requireApi: () => api }));
vi.mock("../remote/directTransport", () => ({ getDirectTransport: () => null }));

import { ChatPane } from "./ChatPane";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { _resetComposerHistoryForTests } from "../hooks/composerHistory";
import type { ChatTab } from "../types/panel";

const chat = { id: "chat-1", name: "Builder", title: "Builder", codingAgent: "codex", model: "gpt-6.1-sol" } as unknown as ChatTab;

function Pane() {
  return (
    <ConfirmModalProvider>
      <ChatPane chat={chat} visible allChats={[chat]} onOpenChat={() => {}} isAgentRunning={false} />
    </ConfirmModalProvider>
  );
}

function box(): HTMLElement {
  return screen.getByRole("textbox");
}

function typeInto(text: string) {
  const el = box();
  el.focus();
  el.textContent = text;
  // The caret ends up after the typed text, as when a person types it.
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  window.getSelection()!.removeAllRanges();
  window.getSelection()!.addRange(range);
  fireEvent.input(el);
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  _resetComposerHistoryForTests();
  vi.unstubAllGlobals();
});

it("Ctrl+Z and Ctrl+Y walk the composer back and forth, also after the tab is closed and reopened", async () => {
  const first = render(<Pane />);
  await act(async () => {});
  typeInto("fix");
  await act(async () => {});
  // A paste is written in code: the browser's own undo could not take it back.
  fireEvent.paste(box(), { clipboardData: { getData: () => " the sidebar" } });
  await act(async () => {});
  expect(box().textContent).toBe("fix the sidebar");

  fireEvent.keyDown(box(), { key: "z", ctrlKey: true });
  await act(async () => {});
  expect(box().textContent).toBe("fix");
  fireEvent.keyDown(box(), { key: "y", ctrlKey: true });
  await act(async () => {});
  expect(box().textContent).toBe("fix the sidebar");

  first.unmount(); // the chat tab closes
  render(<Pane />);
  await act(async () => {});
  expect(box().textContent).toBe("fix the sidebar");
  fireEvent.keyDown(box(), { key: "z", ctrlKey: true });
  await act(async () => {});
  expect(box().textContent).toBe("fix");
});
