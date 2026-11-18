// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const pick = vi.fn(async () => ({ ok: true }));
vi.mock("../hooks/usePanelApi", async (orig) => ({
  ...(await orig<typeof import("../hooks/usePanelApi")>()),
  getApi: () => ({ native_menu_pick: pick }),
}));

const { NativeTextMenu } = await import("./NativeTextMenu");

afterEach(() => {
  cleanup();
  pick.mockClear();
});

it("shows WebView2's spelling suggestions as Ducky's menu and answers with the pick", () => {
  render(<NativeTextMenu />);
  fireEvent.contextMenu(window, { clientX: 40, clientY: 60 });
  act(() => {
    window.__uefnPanelPush?.({
      type: "native_menu",
      token: "t1",
      items: [{ id: 11, label: "villager" }, { separator: true }, { id: 20, label: "Cut" }],
    } as never);
  });
  fireEvent.click(screen.getByRole("menuitem", { name: "villager" }));
  expect(pick).toHaveBeenCalledWith("t1", 11);
  expect(pick).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("menu")).toBeNull();
});

it("shows every Windows spelling fix above Cut / Copy / Paste, and Add to dictionary answers -2", () => {
  // Nov 18 2026: the launcher's window sent only WebView2's own rows, so a misspelled word had
  // its red underline and a menu with no fix in it. Its rows now look like these.
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  render(<NativeTextMenu />);
  act(() => {
    window.__uefnPanelPush?.({
      type: "native_menu",
      token: "m7",
      items: [
        { id: -1, label: "read", enabled: true, replace: "read" },
        { id: -1, label: "red", enabled: true, replace: "red" },
        { id: -1, label: "redo", enabled: true, replace: "redo" },
        { separator: true },
        { id: -2, label: "Add to dictionary", enabled: true },
        { separator: true },
        { id: 50151, label: "Cut", enabled: true },
        { id: 50153, label: "Paste", enabled: true },
      ],
    } as never);
  });
  const names = screen.getAllByRole("menuitem").map((el) => el.textContent);
  expect(names).toEqual(["read", "red", "redo", "Add to dictionary", "Cut", "Paste"]);
  // Three fixes share id -1: each still needs a key of its own.
  expect(errors.mock.calls.filter((c) => String(c[0]).includes("same key"))).toEqual([]);
  errors.mockRestore();
  fireEvent.click(screen.getByRole("menuitem", { name: "Add to dictionary" }));
  expect(pick).toHaveBeenCalledWith("m7", -2);
});

it("types a Windows spelling fix over the selected word and runs no WebView2 command", () => {
  const insert = vi.fn(() => true);
  document.execCommand = insert as never;
  render(<NativeTextMenu />);
  act(() => {
    window.__uefnPanelPush?.({
      type: "native_menu",
      token: "t2",
      items: [{ id: -1, label: "food", replace: "food" }, { id: -2, label: "Add to dictionary" }],
    } as never);
  });
  fireEvent.click(screen.getByRole("menuitem", { name: "food" }));
  expect(insert).toHaveBeenCalledWith("insertText", false, "food");
  expect(pick).toHaveBeenCalledWith("t2", -1);
});
