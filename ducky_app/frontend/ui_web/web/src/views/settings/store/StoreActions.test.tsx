// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoreActions } from "./StoreActions";
import type { DuckyOSStoreItemDto } from "../../../types/panel";

vi.mock("./pluginStoreActions", () => ({
  loadPluginContributes: () => Promise.resolve({}),
  openInstalledPluginSettings: vi.fn(),
  pluginHasWalkthrough: () => false,
  redoInstalledPluginWalkthrough: vi.fn(),
  settingsTargetFromContributes: () => null,
}));

const handlers = { onInstall: vi.fn(), onToggle: vi.fn(), onUninstall: vi.fn() };
afterEach(cleanup);

describe("StoreActions", () => {
  it("keeps Uninstall next to Update while an update is pending", () => {
    const item = { slug: "island-media-kit", kind: "plugin", state: "update", enabled: true } as unknown as DuckyOSStoreItemDto;
    render(<StoreActions item={item} handlers={handlers} busy={false} size="detail" />);
    expect(screen.getByText("Update")).toBeTruthy();
    expect(screen.getByText("Uninstall")).toBeTruthy();
  });

  const windowsOnly = (host: string) =>
    ({
      slug: "handmouse",
      kind: "plugin",
      state: "available",
      platforms: ["windows-x86_64", "windows-arm64"],
      host_platform: host,
    }) as unknown as DuckyOSStoreItemDto;

  it("greys out Install for a Windows-only entry on a Mac", () => {
    render(<StoreActions item={windowsOnly("macos-aarch64")} handlers={handlers} busy={false} size="card" />);
    const btn = screen.getByText("Not available on this system") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("keeps Install for the same entry on windows-x64, and for source plugins", () => {
    const { unmount } = render(<StoreActions item={windowsOnly("windows-x64")} handlers={handlers} busy={false} size="card" />);
    expect((screen.getByText("Install") as HTMLButtonElement).disabled).toBe(false);
    unmount();
    const source = { slug: "notes", kind: "plugin", state: "available", host_platform: "macos-arm64" } as unknown as DuckyOSStoreItemDto;
    render(<StoreActions item={source} handlers={handlers} busy={false} size="card" />);
    expect((screen.getByText("Install") as HTMLButtonElement).disabled).toBe(false);
  });

  it("accepts macos-arm64 and macos-aarch64 as the same key", () => {
    const item = { slug: "x", kind: "plugin", state: "available", platforms: ["macos-arm64"], host_platform: "macos-aarch64" } as unknown as DuckyOSStoreItemDto;
    render(<StoreActions item={item} handlers={handlers} busy={false} size="card" />);
    expect((screen.getByText("Install") as HTMLButtonElement).disabled).toBe(false);
  });
});
