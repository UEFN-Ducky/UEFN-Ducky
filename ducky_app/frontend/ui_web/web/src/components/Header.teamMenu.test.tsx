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
vi.mock("./TeamLiveDropdown", () => ({ TeamLiveDropdown: () => <div data-testid="builtin-team" /> }));
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

describe("Header team menu: built-in until the plugin contributes one", () => {
  it("an account plugin without header.menus keeps the built-in team menu", () => {
    renderHeader();
    expect(screen.getByTestId("builtin-team")).toBeTruthy();
  });

  it("another plugin's menu doesn't replace it", () => {
    menus = [{ id: "notes", ui: "panel:notes", plugin_id: "notes" }];
    renderHeader();
    expect(screen.getByTestId("builtin-team")).toBeTruthy();
    expect(screen.getByTestId("plugin-menu-notes")).toBeTruthy();
  });

  it("a `team` menu shows the plugin's only", () => {
    menus = [{ id: "team", ui: "panel:account-team-menu", plugin_id: "account" }];
    renderHeader();
    expect(screen.queryByTestId("builtin-team")).toBeNull();
    expect(screen.getByTestId("plugin-menu-team")).toBeTruthy();
  });
});
