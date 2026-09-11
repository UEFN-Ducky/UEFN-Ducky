import { afterEach, describe, expect, it } from "vitest";
import {
  newlyEnabledForWalkthrough,
  rememberEnabledPlugin,
  resetFirstEnableSeenForTests,
} from "./firstEnable";

describe("newlyEnabledForWalkthrough", () => {
  afterEach(() => {
    resetFirstEnableSeenForTests();
  });

  it("does not start tours on the first non-empty snapshot", () => {
    expect(newlyEnabledForWalkthrough(["anthropic", "cursor"])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["anthropic", "cursor"])).toEqual([]);
  });

  it("ignores an empty first payload so a later reload cannot look like first-enable", () => {
    expect(newlyEnabledForWalkthrough([])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["anthropic", "cursor", "openai"])).toEqual([]);
  });

  it("does not start when a plugin drops then returns (Store update)", () => {
    expect(newlyEnabledForWalkthrough(["anthropic", "cursor"])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["cursor"])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["anthropic", "cursor"])).toEqual([]);
  });

  it("starts only when exactly one new plugin appears after seed", () => {
    expect(newlyEnabledForWalkthrough(["cursor"])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["cursor", "anthropic"])).toEqual(["anthropic"]);
  });

  it("does not start a batch of new plugins (Update All / contrib refill)", () => {
    expect(newlyEnabledForWalkthrough(["meshy"])).toEqual([]);
    expect(newlyEnabledForWalkthrough(["meshy", "anthropic", "openai"])).toEqual([]);
  });

  it("rememberEnabledPlugin blocks a later first-enable for that slug", () => {
    expect(newlyEnabledForWalkthrough(["cursor"])).toEqual([]);
    rememberEnabledPlugin("plugin.anthropic");
    expect(newlyEnabledForWalkthrough(["cursor", "anthropic"])).toEqual([]);
  });
});
