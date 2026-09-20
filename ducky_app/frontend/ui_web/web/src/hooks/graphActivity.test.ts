import { afterEach, describe, expect, it } from "vitest";
import {
  _peekBackgroundJobsForTests,
  _resetBackgroundActivityForTests,
} from "./backgroundActivity";
import {
  applyBackgroundJobPush,
  graphJobId,
  requestFocusGraph,
  takePendingGraphFocus,
  workflowIdFromJobId,
  syncReadyGraphJobs,
} from "./graphActivity";

afterEach(() => {
  takePendingGraphFocus("pipeline");
  takePendingGraphFocus("automation");
  _resetBackgroundActivityForTests();
});

describe("graphActivity", () => {
  it("lists enabled graphs as ready and drops them when disabled", () => {
    syncReadyGraphJobs(
      [
        { id: "a", name: "Nightly", kind: "automation", enabled: true, node_count: 2 },
        { id: "p", name: "Art", kind: "pipeline", enabled: true, node_count: 3 },
        { id: "off", name: "Off", kind: "automation", enabled: false, node_count: 2 },
        { id: "empty", name: "Empty", kind: "pipeline", enabled: true, node_count: 0 },
      ],
      [],
    );
    const jobs = _peekBackgroundJobsForTests();
    expect(jobs.map((j) => j.id).sort()).toEqual([graphJobId("a"), graphJobId("p")].sort());
    expect(jobs.every((j) => j.phase === "ready" && j.detail === "Ready to run")).toBe(true);

    syncReadyGraphJobs([{ id: "p", name: "Art", kind: "pipeline", enabled: true, node_count: 3 }], jobs);
    expect(_peekBackgroundJobsForTests().map((j) => j.id)).toEqual([graphJobId("p")]);
  });

  it("does not clobber a running graph with ready", () => {
    applyBackgroundJobPush({
      type: "background_job",
      id: graphJobId("a"),
      title: "Nightly",
      phase: "working",
      detail: "Running",
    });
    syncReadyGraphJobs(
      [{ id: "a", name: "Nightly", kind: "automation", enabled: true, node_count: 2 }],
      _peekBackgroundJobsForTests(),
    );
    expect(_peekBackgroundJobsForTests()[0]?.phase).toBe("working");
  });

  it("parses workflow id from live and finished job ids", () => {
    expect(workflowIdFromJobId(graphJobId("abc"))).toBe("abc");
    expect(workflowIdFromJobId("graph-run:abc:171000")).toBe("abc");
  });

  it("queues a graph focus for the editor", () => {
    requestFocusGraph("pipeline", "play");
    expect(takePendingGraphFocus("automation")).toBe("");
    expect(takePendingGraphFocus("pipeline")).toBe("play");
    expect(takePendingGraphFocus("pipeline")).toBe("");
  });
});
