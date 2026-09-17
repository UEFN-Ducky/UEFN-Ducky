// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StoreDetailView } from "./StoreDetailView";
import type { StoreItemHandlers } from "./StoreActions";

const handlers: StoreItemHandlers = {
  onInstall: vi.fn(),
  onBuy: vi.fn(),
  onToggle: vi.fn(),
  onUninstall: vi.fn(),
};

describe("StoreDetailView pending slug", () => {
  it("renders a loading pane instead of null while the catalog row is missing", () => {
    render(
      <StoreDetailView
        item={null}
        pendingSlug="blender"
        jobs={{}}
        actionBusy={{}}
        handlers={handlers}
        onBack={() => {}}
      />,
    );
    expect(screen.getByText("Back")).toBeTruthy();
    expect(screen.getByText("Loading blender…")).toBeTruthy();
  });

  it("returns nothing when there is no item and no pending slug", () => {
    const { container } = render(
      <StoreDetailView item={null} jobs={{}} actionBusy={{}} handlers={handlers} onBack={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });
});
