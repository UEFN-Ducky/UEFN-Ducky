// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ set_visible_terminals: vi.fn(async () => true) }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../tabs/tabRegistryClient", () => ({ WINDOW_ID: "main" }));

import { _resetVisibleTerminalsForTests, setTerminalShown } from "./visibleTerminals";

const flush = () => new Promise<void>((resolve) => queueMicrotask(resolve));

describe("visibleTerminals", () => {
  beforeEach(() => {
    _resetVisibleTerminalsForTests();
    api.set_visible_terminals.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("reports shown panes once per change, and a hidden pane as hidden", async () => {
    setTerminalShown("t1", true);
    setTerminalShown("t2", true);
    await flush();
    expect(api.set_visible_terminals).toHaveBeenCalledTimes(1);
    expect(api.set_visible_terminals).toHaveBeenLastCalledWith(["t1", "t2"], "main");

    setTerminalShown("t2", true); // no change: nothing sent
    await flush();
    expect(api.set_visible_terminals).toHaveBeenCalledTimes(1);

    setTerminalShown("t1", false); // the person switched tabs: t1's pane is hidden
    await flush();
    expect(api.set_visible_terminals).toHaveBeenLastCalledWith(["t2"], "main");
  });

  it("a tab switch (hide one, show another) is one report", async () => {
    setTerminalShown("t1", true);
    await flush();
    setTerminalShown("t1", false);
    setTerminalShown("t2", true);
    await flush();
    expect(api.set_visible_terminals.mock.calls).toEqual([[["t1"], "main"], [["t2"], "main"]]);
  });

  it("a hidden window shows no terminals", async () => {
    setTerminalShown("t1", true);
    await flush();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(api.set_visible_terminals).toHaveBeenLastCalledWith([], "main");
    hidden.mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(api.set_visible_terminals).toHaveBeenLastCalledWith(["t1"], "main");
  });
});
