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
  it("does not list idle graphs as ready to run", () => {
    applyBackgroundJobPush({
      type: "background_job",
      id: graphJobId("idle"),
      title: "Image to island",
      phase: "ready",
      detail: "Ready to run",
    });
    syncReadyGraphJobs(
      [
        { id: "idle", name: "Image to island", kind: "pipeline", enabled: true, node_count: 3 },
        { id: "a", name: "Nightly", kind: "automation", enabled: true, node_count: 2 },
      ],
      _peekBackgroundJobsForTests(),
    );
    expect(_peekBackgroundJobsForTests().some((j) => j.phase === "ready")).toBe(false);
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
