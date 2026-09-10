import { describe, expect, it } from "vitest";
import { parseCodingAgentLoginHref } from "./openCodingAgentLogin";

describe("parseCodingAgentLoginHref", () => {
  it("accepts the Settings login deep link", () => {
    expect(parseCodingAgentLoginHref("ducky://settings.llms/anthropic#login")).toEqual({
      providerId: "anthropic",
    });
  });

  it("rejects color tokens and http urls", () => {
    expect(parseCodingAgentLoginHref("ducky:blue")).toBeNull();
    expect(parseCodingAgentLoginHref("https://claude.com/login")).toBeNull();
  });
});
