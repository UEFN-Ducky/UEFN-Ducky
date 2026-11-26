// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { markNativeWindowChromeBody } from "../utils/nativeWindowChrome";

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

const { Header } = await import("./Header");

function setPlatform(platform: string | undefined) {
  (window as unknown as { pywebview?: { platform?: string } }).pywebview = platform ? { platform } : undefined;
}

function headerShape() {
  const { container } = render(
    <Header isOnline={false} layoutMode="full" cycleLayoutMode={() => {}} hasProject={false} />,
  );
  const header = container.querySelector("header")!;
  return {
    platform: header.getAttribute("data-platform"),
    mac: header.classList.contains("app-header--mac"),
    windowControls: !!header.querySelector(".window-controls"),
    dragRegion: header.classList.contains("drag-region"),
  };
}

afterEach(() => {
  cleanup();
  setPlatform(undefined);
  delete document.documentElement.dataset.platform;
});

describe("Header on macOS", () => {
  it("leaves the left corner to the traffic lights and drops our window buttons", () => {
    setPlatform("cocoa");
    expect(headerShape()).toMatchInlineSnapshot(`
      {
        "dragRegion": true,
        "mac": true,
        "platform": "mac",
        "windowControls": false,
      }
    `);
    markNativeWindowChromeBody();
    expect(document.documentElement.dataset.platform).toBe("mac");
  });

  it("keeps the Windows header as it was", () => {
    setPlatform("edgechromium");
    expect(headerShape()).toMatchInlineSnapshot(`
      {
        "dragRegion": true,
        "mac": false,
        "platform": null,
        "windowControls": true,
      }
    `);
  });
});
