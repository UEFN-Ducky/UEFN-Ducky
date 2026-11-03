// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePanelApi")>()),
  getApi: () => null,
  isRemote: () => false,
}));
vi.mock("../hooks/useNarrowLayout", () => ({ useNarrowLayout: () => false }));
vi.mock("../contexts/QuickOpenBridge", () => ({ useQuickOpenBridge: () => ({ openPalette: () => {} }) }));
vi.mock("./ProjectSelector", () => ({ ProjectSelector: () => null }));
vi.mock("./ConnectionStatusDropdown", () => ({ ConnectionStatusDropdown: () => null }));
vi.mock("./BackgroundActivityDropdown", () => ({ BackgroundActivityDropdown: () => null }));
vi.mock("./PluginHeaderMenu", () => ({
  PluginMenuButton: ({ menu }: { menu: { id: string } }) => <div data-testid={`plugin-menu-${menu.id}`} />,
}));
let menus: Array<{ id: string; ui: string; plugin_id: string }> = [];
vi.mock("../hooks/usePluginContributions", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePluginContributions")>()),
  usePluginContributions: () => ({ header_buttons: [], header_menus: menus }),
}));

const { Header } = await import("./Header");
const renderHeader = () =>
  render(<Header isOnline={false} layoutMode="full" cycleLayoutMode={() => {}} hasProject />);

afterEach(() => {
  cleanup();
  menus = [];
});

describe("Header team menu: Ducky Account's header.menus entry", () => {
  it("no plugin menu: no team menu in the title bar", () => {
    renderHeader();
    expect(screen.queryByTestId("plugin-menu-team")).toBeNull();
  });

  it("the account plugin's `team` menu shows next to other plugins' menus", () => {
    menus = [
      { id: "notes", ui: "panel:notes", plugin_id: "notes" },
      { id: "team", ui: "panel:account-team-menu", plugin_id: "account" },
    ];
    renderHeader();
    expect(screen.getByTestId("plugin-menu-team")).toBeTruthy();
    expect(screen.getByTestId("plugin-menu-notes")).toBeTruthy();
  });
});
