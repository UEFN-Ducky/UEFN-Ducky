// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import fixture from "../../../../../../backend/workspace/schemas/fixtures/changeset_run.json";
import type { ChangesetRunDto } from "../../types/panel";

const listChangesets = vi.fn();

vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({ list_changesets: listChangesets }),
}));
vi.mock("../../hooks/useAgentEventBus", () => ({
  subscribeAgentEvents: () => () => {},
}));
vi.mock("../../contexts/ConfirmModalContext", () => ({
  useConfirmModal: () => ({ confirm: async () => true }),
}));

const { ChangesView } = await import("./ChangesView");

const run = fixture as unknown as ChangesetRunDto;

beforeEach(() => {
  listChangesets.mockReset();
  listChangesets.mockResolvedValue([run]);
  // jsdom has no ResizeObserver; the view only uses it to track the viewport.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The ducky name is in both the run header and the filter dropdown. */
function runHeading(container: HTMLElement): string {
  return container.querySelector(".changes-run-name")?.textContent ?? "";
}

describe("ChangesView", () => {
  it("shows the run, who made it, and every kind of change in order", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));

    // The file writes, the editor change, and the refusal all read as one story.
    expect(screen.getByText("shop.verse")).toBeTruthy();
    expect(screen.getByText("VerifyCube")).toBeTruthy();
    expect(screen.getByText("moved +250 on Z")).toBeTruthy();
    expect(screen.getByText("BLOCKED")).toBeTruthy();
    expect(screen.getByText(/not in your write lane/i)).toBeTruthy();
  });

  it("counts the blocked attempt apart from the work that landed", async () => {
    const { container } = render(<ChangesView />);
    await waitFor(() => expect(runHeading(container)).toBe("Hacker"));
    expect(container.querySelector(".changeset-badge--blocked")?.textContent).toBe("1 blocked");
    // Two file writes, one editor change and the refusal: all four are changes.
    expect(container.querySelector(".changes-toolbar-count")?.textContent).toBe("4 changes");
  });

  it("the kind filter narrows to editor changes alone", async () => {
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Filter by kind"), { target: { value: "editor" } });
    expect(screen.queryByText("shop.verse")).toBeNull();
    expect(screen.queryByText("BLOCKED")).toBeNull();
    expect(screen.getByText("VerifyCube")).toBeTruthy();
  });

  it("search matches editor targets, not just file paths", async () => {
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText("shop.verse")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Search changes"), { target: { value: "verifycube" } });
    expect(screen.getByText("VerifyCube")).toBeTruthy();
    expect(screen.queryByText("shop.verse")).toBeNull();
  });

  it("says so plainly when the project has no history yet", async () => {
    listChangesets.mockResolvedValue([]);
    render(<ChangesView />);
    await waitFor(() => expect(screen.getByText(/Nothing has been changed/)).toBeTruthy());
  });
});
