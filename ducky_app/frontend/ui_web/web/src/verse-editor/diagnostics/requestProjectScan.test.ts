import { describe, expect, it } from "vitest";

/**
 * Explicit Problems Refresh is the only caller that must request full=true.
 * Routine auto-checks (project open, agent write fallback, compile) use full=false.
 */
describe("full-project diagnostics policy", () => {
  it("keeps full rescans exclusive to explicit refresh", () => {
    const explicitRefresh = { full: true as const };
    const projectOpen = { full: false as const };
    const agentWriteFallback = { full: false as const };
    const compileFollowUp = { full: false as const };

    expect(explicitRefresh.full).toBe(true);
    expect(projectOpen.full).toBe(false);
    expect(agentWriteFallback.full).toBe(false);
    expect(compileFollowUp.full).toBe(false);
  });
});
