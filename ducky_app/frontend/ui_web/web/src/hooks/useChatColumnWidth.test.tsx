// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CHAT_COLUMN_WIDTH_VAR, persistChatColumnWidth, useChatColumnWidth } from "./useChatColumnWidth";

function Column() {
  const { shellRef, setZoomScale } = useChatColumnWidth();
  return <div ref={shellRef} data-testid="shell">
    <button onClick={() => setZoomScale(1.5)}>Zoom in</button>
    <button onClick={() => setZoomScale(1)}>Reset zoom</button>
  </div>;
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("leaves viewport fitting to CSS without a delayed measure/write resize loop", () => {
  const observe = vi.fn();
  vi.stubGlobal("ResizeObserver", class { constructor() { observe(); } observe() {} disconnect() {} });
  const frames = vi.spyOn(window, "requestAnimationFrame");
  const reads = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(300);
  render(<Column />);
  for (let i = 0; i < 100; i++) fireEvent(window, new Event("resize"));
  expect(screen.getByTestId("shell").style.getPropertyValue(CHAT_COLUMN_WIDTH_VAR)).toBe("960px");
  expect(observe).not.toHaveBeenCalled();
  expect(frames).not.toHaveBeenCalled();
  expect(reads).not.toHaveBeenCalled();
});

it("still applies saved column width and zoom without changing the preference", () => {
  localStorage.setItem("uefn-chat-column-width", "640");
  render(<Column />);
  const width = () => screen.getByTestId("shell").style.getPropertyValue(CHAT_COLUMN_WIDTH_VAR);
  expect(width()).toBe("640px");
  fireEvent.click(screen.getByText("Zoom in"));
  expect(width()).toBe("960px");
  expect(localStorage.getItem("uefn-chat-column-width")).toBe("640");
  fireEvent.click(screen.getByText("Reset zoom"));
  expect(width()).toBe("640px");
  act(() => persistChatColumnWidth(720));
  expect(width()).toBe("720px");
});
