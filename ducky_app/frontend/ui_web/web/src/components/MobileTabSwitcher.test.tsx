// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MobileTabSwitcher, setHeaderTabsSlot } from "./MobileTabSwitcher";
import type { EditorTab } from "../types/panel";

afterEach(() => {
  cleanup();
  setHeaderTabsSlot(null);
});

const tabs = [
  { id: "chat:1", kind: "chat", name: "New ducky3", chatId: "1" },
  { id: "workflows:main", kind: "workflows", name: "Workflows" },
] as EditorTab[];

/** Phones: the editor's tabs are a list in the Ducky menu's Tabs tab. */
describe("MobileTabSwitcher", () => {
  it("lists the open tabs, switches (closing the menu) or closes them", () => {
    const onActivate = vi.fn();
    const onClose = vi.fn();
    const closeMenu = vi.fn();
    setHeaderTabsSlot(document.createElement("div"), closeMenu);
    render(<MobileTabSwitcher tabs={tabs} activeTabId="workflows:main" onActivate={onActivate} onClose={onClose} />);
    expect(screen.getByRole("menuitem", { name: "Workflows" }).getAttribute("aria-current")).toBe("true");
    fireEvent.click(screen.getByRole("menuitem", { name: "New ducky3" }));
    expect(onActivate).toHaveBeenCalledWith("chat:1");
    expect(closeMenu).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Close New ducky3" }));
    expect(onClose).toHaveBeenCalledWith("chat:1");
    expect(closeMenu).toHaveBeenCalledTimes(1);
  });

  it("shows nothing without tabs", () => {
    const { container } = render(<MobileTabSwitcher tabs={[]} activeTabId={null} onActivate={() => undefined} onClose={() => undefined} />);
    expect(container.textContent).toBe("");
  });
});
