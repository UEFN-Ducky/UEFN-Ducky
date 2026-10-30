// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ScopePicker } from "./ScopePicker";
import type { TeamScope } from "./teamScopeStore";

afterEach(cleanup);

const scope = (extra: Partial<TeamScope> = {}): TeamScope => ({
  pluginId: "art", plugin: "AI Art Studio",
  status: { ok: true, visible: true, canChange: true, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } },
  phrase: { key: "local", text: "Only on this PC", tone: "" },
  syncing: false, busy: false, switching: false, shared: false, variant: "personal", kind: "team", current: "personal",
  menu: [{ id: "personal", kind: "personal", label: "Local" }, { id: "mp", kind: "shared", label: "Mindful Path" }],
  offers: [{ space: "mp", label: "Mindful Path" }], refusal: "", manual: false, canSync: false, failing: false,
  canPush: false, canPull: false, badge: 0, conflicts: 0, notes: [],
  actions: {
    pull: vi.fn(), push: vi.fn(), syncNow: vi.fn(), choose: vi.fn(async () => {}), copyInto: vi.fn(async () => {}),
    settle: vi.fn(), loadChoices: vi.fn(async () => {}), refresh: vi.fn(),
  },
  ...extra,
} as TeamScope);

describe("ScopePicker", () => {
  it("one button shows the current choice; its dropdown lists the choices and picking one switches", () => {
    const s = scope();
    render(<ScopePicker scope={s} />);
    const trigger = screen.getByRole("button", { name: "Whose data: Local" });
    expect(trigger.getAttribute("aria-haspopup")).toBe("listbox");
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.click(trigger);
    expect(screen.getByRole("option", { name: /^Local/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByRole("option", { name: /Mindful Path/ }));
    expect(s.actions.choose).toHaveBeenCalledWith(expect.objectContaining({ id: "mp" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("arrow keys and Enter pick; Copy Local into a team is offered at the bottom", () => {
    const s = scope();
    render(<ScopePicker scope={s} />);
    const trigger = screen.getByRole("button", { name: "Whose data: Local" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy Local into Mindful Path" }));
    expect(s.actions.copyInto).toHaveBeenCalledWith({ space: "mp", label: "Mindful Path" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(s.actions.choose).toHaveBeenCalledWith(expect.objectContaining({ id: "mp" }));
  });

  it("is disabled while switching, loading, or without a project", () => {
    const view = render(<ScopePicker scope={scope({ switching: true })} />);
    expect((screen.getByRole("button", { name: /^Whose data/ }) as HTMLButtonElement).disabled).toBe(true);
    view.unmount();
    render(<ScopePicker scope={scope({ menu: null })} />);
    expect((screen.getByRole("button", { name: "Whose data: Loading…" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
