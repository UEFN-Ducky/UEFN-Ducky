// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HeaderMenuHost } from "../plugin-ui/bridge";

let page: { tabId: string; visible?: boolean; headerMenu?: HeaderMenuHost } | null = null;
vi.mock("../plugin-ui", () => ({
  PluginWebviewPane: (props: { tabId: string; visible?: boolean; headerMenu?: HeaderMenuHost }) => {
    page = props;
    return <div data-testid="page" data-visible={String(props.visible)} />;
  },
}));

const { PluginMenuButton } = await import("./PluginHeaderMenu");
const { handleBridgeRequest } = await import("../plugin-ui/bridge");
const { BRIDGE_CHANNEL } = await import("../plugin-ui/constants");

const MENU = { id: "team", title: "Team", icon: "👥", ui: "panel:account-team-menu", width: 320, plugin_id: "account" };

/** What the plugin page's postMessage call does, through the real bridge. */
const call = (method: string, params: Record<string, unknown> = {}, headerMenu = page?.headerMenu) =>
  act(() => handleBridgeRequest({ pluginId: "account", panelId: "account-team-menu", headerMenu },
    { channel: BRIDGE_CHANNEL, id: method, method, params }));

afterEach(() => {
  cleanup();
  page = null;
});

describe("PluginMenuButton (contributes.header.menus)", () => {
  it("renders the button with the plugin's icon and page, kept loaded while closed", () => {
    render(<PluginMenuButton menu={MENU} />);
    const btn = screen.getByRole("button", { name: "Team" });
    expect(btn.textContent).toContain("👥");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(page?.tabId).toBe("plugin:account:account-team-menu~header");
    expect(screen.getByTestId("page").getAttribute("data-visible")).toBe("false");
  });

  it("opens the dropdown with the page shown", () => {
    render(<PluginMenuButton menu={MENU} />);
    fireEvent.click(screen.getByRole("button", { name: "Team" }));
    expect(screen.getByRole("button", { name: "Team" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("page").getAttribute("data-visible")).toBe("true");
    expect(document.querySelector(".dropdown-panel--parked")).toBeNull();
  });

  it("header_menu_badge sets the dot and count; bad values clear them", async () => {
    render(<PluginMenuButton menu={MENU} />);
    const res = await call("header_menu_badge", { dot: "error", count: 3 });
    expect(res.ok).toBe(true);
    const btn = screen.getByRole("button", { name: "Team" });
    expect(btn.querySelector('[data-dot="error"]')).toBeTruthy();
    expect(btn.querySelector(".app-header-team-btn__count")?.textContent).toBe("3");
    await call("header_menu_badge", { dot: "purple", count: -2 });
    expect(btn.querySelector("[data-dot]")).toBeNull();
    expect(btn.querySelector(".app-header-team-btn__count")).toBeNull();
  });

  it("header_menu_close closes the dropdown", async () => {
    render(<PluginMenuButton menu={MENU} />);
    fireEvent.click(screen.getByRole("button", { name: "Team" }));
    const res = await call("header_menu_close");
    expect(res.ok).toBe(true);
    expect(screen.getByRole("button", { name: "Team" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("page").getAttribute("data-visible")).toBe("false");
  });

  it("header_menu_size sizes the dropdown to the page, kept inside the window", async () => {
    render(<PluginMenuButton menu={MENU} />);
    fireEvent.click(screen.getByRole("button", { name: "Team" }));
    const box = () => document.querySelector(".plugin-header-menu") as HTMLElement;
    expect(box().style.height).toBe("240px");
    await call("header_menu_size", { height: 300 });
    expect(box().style.height).toBe("300px");
    await call("header_menu_size", { height: 5000 });
    expect(parseInt(box().style.height, 10)).toBe(Math.max(120, Math.min(640, window.innerHeight - 96)));
    await call("header_menu_size", { height: 40 });
    expect(box().style.height).toBe("120px");
    await call("header_menu_size", { height: "x" });
    expect(box().style.height).toBe("120px");
  });

  it("headerMenuHeight clamps to 120 px and the window", async () => {
    const { headerMenuHeight } = await import("./PluginHeaderMenu");
    expect(headerMenuHeight(300, 900)).toBe(300);
    expect(headerMenuHeight(900, 900)).toBe(640);
    expect(headerMenuHeight(500, 500)).toBe(404);
    expect(headerMenuHeight(10, 900)).toBe(120);
  });

  it("a page outside a header menu can't call them", async () => {
    const res = await call("header_menu_close", {}, undefined);
    expect(res.ok).toBe(false);
  });
});
