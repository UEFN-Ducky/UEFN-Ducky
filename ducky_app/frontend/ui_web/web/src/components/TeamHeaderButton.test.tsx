// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { publishTeamScope, unpublishTeamScope, type TeamScope } from "../plugin-ui/teamScopeStore";
import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { OPEN_TEAM_MODAL_EVENT } from "../plugin-ui/scopeActivity";
import { TeamHeaderButton } from "./TeamHeaderButton";

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({}), isRemote: () => false }));

afterEach(() => {
  act(() => unpublishTeamScope("island-media-kit"));
  cleanup();
});

const scope = (extra: Partial<TeamScope> = {}): TeamScope => ({
  pluginId: "island-media-kit", plugin: "Island Media Kit",
  status: { ok: true, visible: true, canChange: false, scope: { kind: "shared", label: "Mindful Path", spaceId: "s1", readOnly: false } },
  phrase: { key: "up_to_date", text: "Up to date", tone: "" },
  syncing: false, busy: false, switching: false, shared: true, variant: "shared", kind: "team", current: "s1",
  menu: [], offers: [], refusal: "", manual: false, canSync: true, failing: false, canPush: false, canPull: false,
  badge: 0, conflicts: 0, notes: [],
  actions: {
    pull: vi.fn(), push: vi.fn(), syncNow: vi.fn(), choose: vi.fn(), copyInto: vi.fn(), settle: vi.fn(),
    loadChoices: vi.fn(async () => {}), refresh: vi.fn(),
  },
  ...extra,
} as TeamScope);

describe("TeamHeaderButton", () => {
  it("is hidden while no plugin pane shows a team bar", () => {
    const { container } = render(<TeamHeaderButton />);
    expect(container.textContent).toBe("");
  });

  it("shows the team and status, the count red with conflicts, and opens the Team modal", () => {
    render(<TeamHeaderButton />);
    act(() => publishTeamScope(scope({ badge: 2, conflicts: 2 })));
    const button = screen.getByRole("button", { name: "Team: Mindful Path · Up to date" });
    expect(button.getAttribute("title")).toBe("Mindful Path · Up to date");
    const count = button.querySelector(".app-header-team-btn__count");
    expect(count?.textContent).toBe("2");
    expect(count?.classList.contains("is-alert")).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("Mindful Path · Island Media Kit")).toBeTruthy();
  });

  it("the tray's Resolve opens the Team modal on Conflicts", () => {
    render(<ConfirmModalProvider><TeamHeaderButton /></ConfirmModalProvider>);
    act(() => publishTeamScope(scope({
      badge: 1, conflicts: 1,
      status: {
        ok: true, visible: true, canChange: false, conflicts: [{ kind: "doc", key: "deck", name: "deck", by: "Ana", reason: "both" }],
        scope: { kind: "shared", label: "Mindful Path", spaceId: "s1", readOnly: false },
      },
    })));
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => {
      window.dispatchEvent(new CustomEvent(OPEN_TEAM_MODAL_EVENT, { detail: { section: "conflicts" } }));
    });
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Conflicts/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("region", { name: "Source control: Mindful Path" })).toBeTruthy();
  });

  it("on Local it still shows, so a team can be picked", () => {
    render(<TeamHeaderButton />);
    act(() => publishTeamScope(scope({
      shared: false, variant: "personal", current: "personal",
      status: { ok: true, visible: true, scope: { kind: "personal", label: "Local", spaceId: "", readOnly: false } },
      phrase: { key: "local", text: "Only on this PC", tone: "" },
    })));
    expect(screen.getByRole("button", { name: "Team: Local · Only on this PC" })).toBeTruthy();
  });
});
