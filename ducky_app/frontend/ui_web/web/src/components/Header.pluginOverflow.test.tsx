// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePanelApi")>()),
  getApi: () => null,
  isRemote: () => false,
}));
vi.mock("../hooks/useNarrowLayout", () => ({ useNarrowLayout: () => true })); // folds plugins into the menu
vi.mock("../contexts/QuickOpenBridge", () => ({ useQuickOpenBridge: () => ({ openPalette: () => {} }) }));
vi.mock("./ProjectSelector", () => ({ ProjectSelector: () => null }));
vi.mock("./ConnectionStatusDropdown", () => ({ ConnectionStatusDropdown: () => null }));
vi.mock("./BackgroundActivityDropdown", () => ({ BackgroundActivityDropdown: () => null }));
vi.mock("../hooks/usePluginContributions", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePluginContributions")>()),
  usePluginContributions: () => ({
    header_buttons: [{ id: "board", plugin_id: "kanban", title: "Kanban", action: "panel:board", icon: "🗂" }],
  }),
}));

const { Header } = await import("./Header");

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("Header Plugins overflow menu", () => {
  it("does not wear the header.menus page class (240 px tall: the icon left the header)", () => {
    render(<Header isOnline={false} layoutMode="full" cycleLayoutMode={() => {}} hasProject={false} />);
    const wrap = screen.getByRole("button", { name: "Plugins" }).parentElement!;
    expect(wrap.classList.contains("plugin-header-overflow")).toBe(true);
    expect(wrap.classList.contains("plugin-header-menu")).toBe(false);
  });
});
