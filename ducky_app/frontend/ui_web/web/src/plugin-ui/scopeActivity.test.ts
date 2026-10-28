// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _peekBackgroundJobsForTests, _resetBackgroundActivityForTests, requestBackgroundJobAction } from "../hooks/backgroundActivity";
import { DONE_LINGER_MS, installScopeActivity, OPEN_TEAM_MODAL_EVENT } from "./scopeActivity";
import { publishTeamScope, unpublishTeamScope, type TeamScope } from "./teamScopeStore";

const scope = (extra: Partial<TeamScope> = {}, status: TeamScope["status"] = {}): TeamScope => ({
  pluginId: "notes",
  plugin: "Notes",
  shared: true,
  syncing: false,
  busy: false,
  conflicts: 0,
  status: { scope: { kind: "shared", label: "Mindful Path", spaceId: "sp1", readOnly: false }, ...status },
  ...extra,
} as TeamScope);

describe("scopeActivity", () => {
  let stop: () => void;
  beforeEach(() => {
    vi.useFakeTimers();
    _resetBackgroundActivityForTests();
    stop = installScopeActivity();
  });
  afterEach(() => {
    stop();
    unpublishTeamScope("notes");
    vi.useRealTimers();
  });

  it("shows one working row while syncing", () => {
    publishTeamScope(scope({ syncing: true }, { pending: 3 }));
    publishTeamScope(scope({ syncing: true }, { pending: 3 }));
    const jobs = _peekBackgroundJobsForTests();
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ id: "team-sync:sp1", source: "team", title: "Mindful Path · Syncing", detail: "Sending 3 changes", phase: "working" });
  });

  it("says what comes in", () => {
    publishTeamScope(scope({ busy: true }, { changes: { incoming: 5 } }));
    expect(_peekBackgroundJobsForTests()[0].detail).toBe("Getting 5 changes");
  });

  it("marks done, then clears itself", () => {
    publishTeamScope(scope({ syncing: true }));
    publishTeamScope(scope());
    expect(_peekBackgroundJobsForTests()[0].phase).toBe("done");
    vi.advanceTimersByTime(DONE_LINGER_MS);
    expect(_peekBackgroundJobsForTests()).toHaveLength(0);
  });

  it("an idle team adds no row", () => {
    publishTeamScope(scope());
    publishTeamScope(scope({ shared: false, syncing: true }));
    expect(_peekBackgroundJobsForTests()).toHaveLength(0);
  });

  it("conflicts make an error row whose button opens the Team modal", () => {
    const opened = vi.fn();
    window.addEventListener(OPEN_TEAM_MODAL_EVENT, opened);
    publishTeamScope(scope({ syncing: true }));
    publishTeamScope(scope({ conflicts: 2 }));
    const jobs = _peekBackgroundJobsForTests();
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ phase: "error", title: "Mindful Path · 2 conflicts", action: "Resolve" });
    vi.advanceTimersByTime(DONE_LINGER_MS * 2);
    expect(_peekBackgroundJobsForTests()).toHaveLength(1);
    requestBackgroundJobAction("team-sync:sp1");
    expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({ section: "conflicts" });
    window.removeEventListener(OPEN_TEAM_MODAL_EVENT, opened);
    publishTeamScope(scope());
    expect(_peekBackgroundJobsForTests()).toHaveLength(0);
  });
});
