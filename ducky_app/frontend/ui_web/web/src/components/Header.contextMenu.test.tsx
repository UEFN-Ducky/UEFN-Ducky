// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
vi.mock("../hooks/usePluginContributions", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePluginContributions")>()),
  usePluginContributions: () => ({
    header_buttons: [
      { id: "account", plugin_id: "account", title: "Ducky Account", action: "settings:Account", icon: "user" },
    ],
  }),
}));

const { Header } = await import("./Header");

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("Header plugin buttons", () => {
  it("show with no project open, so a fresh install shows them at once", () => {
    const { container } = render(
      <Header isOnline={false} layoutMode="full" cycleLayoutMode={() => {}} hasProject={false} />,
    );
    expect(screen.getByRole("button", { name: "Ducky Account" })).toBeTruthy();
    fireEvent.contextMenu(container.querySelector("[data-header-menu]")!);
    expect(screen.getByRole("menuitemcheckbox", { name: "Ducky Account" })).toBeTruthy();
  });
});

describe("Header right-click menu", () => {
  it("hides a header button and shows it again", () => {
    const { container } = render(
      <Header isOnline={false} layoutMode="full" cycleLayoutMode={() => {}} hasProject />,
    );
    expect(screen.getByRole("button", { name: "Ducky Account" })).toBeTruthy();

    fireEvent.contextMenu(container.querySelector("[data-header-menu]")!);
    const row = screen.getByRole("menuitemcheckbox", { name: "Ducky Account" });
    expect(row.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(row);
    expect(screen.queryByRole("button", { name: "Ducky Account" })).toBeNull();
    // The menu stays open and the hidden button is still listed, so it can be added back.
    const again = screen.getByRole("menuitemcheckbox", { name: "Ducky Account" });
    expect(again.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(again);
    expect(screen.getByRole("button", { name: "Ducky Account" })).toBeTruthy();
  });
});
