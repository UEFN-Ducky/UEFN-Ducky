// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoreCard } from "./StoreCard";
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

function card(extra: Partial<DuckyOSStoreItemDto>) {
  const item = { slug: "blender", kind: "plugin", name: "Blender", state: "available", ...extra } as DuckyOSStoreItemDto;
  return render(<StoreCard item={item} busy={null} actionBusy={false} handlers={handlers} onOpen={vi.fn()} />);
}

describe("StoreCard platform badge", () => {
  it("shows 'Windows only' when this machine has no build", () => {
    card({ platform_badge: "Windows only" });
    expect(screen.getByText("Windows only")).toBeTruthy();
  });

  it("a Windows-only entry on a Mac shows the badge and a disabled Install", () => {
    card({ platform_badge: "Windows only", platforms: ["windows-x86_64"], host_platform: "macos-aarch64" });
    expect(screen.getByText("Windows only")).toBeTruthy();
    expect((screen.getByText("Not available on this system") as HTMLButtonElement).disabled).toBe(true);
  });

  it("the same entry on windows-x64 keeps Install enabled", () => {
    card({ platforms: ["windows-x86_64"], host_platform: "windows-x86_64" });
    expect((screen.getByText("Install") as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows no platform badge when the build loads here", () => {
    const { container } = card({});
    expect(container.querySelector(".ds-platform-chip")).toBeNull();
  });
});
