// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

function card(extra: Partial<DuckyOSStoreItemDto>) {
  const item = { slug: "blender", kind: "plugin", name: "Blender", state: "available", ...extra } as DuckyOSStoreItemDto;
  return render(<StoreCard item={item} busy={null} actionBusy={false} handlers={handlers} onOpen={vi.fn()} />);
}

describe("StoreCard platform badge", () => {
  it("shows 'Windows only' when this machine has no build", () => {
    card({ platform_badge: "Windows only" });
    expect(screen.getByText("Windows only")).toBeTruthy();
  });

  it("shows no platform badge when the build loads here", () => {
    const { container } = card({});
    expect(container.querySelector(".ds-platform-chip")).toBeNull();
  });
});
