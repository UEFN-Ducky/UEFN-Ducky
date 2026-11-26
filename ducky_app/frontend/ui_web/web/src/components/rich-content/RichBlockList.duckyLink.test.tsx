// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RichBlockList } from "./RichBlockList";

const duckyLinkOpen = vi.hoisted(() => vi.fn(async () => ({ ok: true })));
vi.mock("../../hooks/usePanelApi", async (original) => ({
  ...(await original<typeof import("../../hooks/usePanelApi")>()),
  getApi: () => ({ ducky_link_open: duckyLinkOpen }),
}));

afterEach(cleanup);

it("a ducky_link block is a button that opens that PC's Ducky", () => {
  const view = render(
    <RichBlockList
      blocks={[
        { type: "callout", tone: "info", text: "Verse diagnostics run on Tas's PC" },
        { type: "ducky_link", key_id: "pc1", name: "Tas's PC" },
      ]}
    />,
  );
  expect(view.container.textContent).toContain("Verse diagnostics run on Tas's PC");
  expect(view.container.textContent).not.toContain("No problems");
  fireEvent.click(view.getByRole("button", { name: "Open Tas's PC" }));
  expect(duckyLinkOpen).toHaveBeenCalledWith("pc1", "Tas's PC");
});
