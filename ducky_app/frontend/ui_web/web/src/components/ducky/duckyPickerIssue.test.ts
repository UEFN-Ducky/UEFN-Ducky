import { describe, expect, it } from "vitest";
import { duckyPickerIssue } from "./duckyPickerIssue";

describe("duckyPickerIssue", () => {
  it("asks for a Store gateway when none are installed", () => {
    const issue = duckyPickerIssue({
      gatewayCount: 0,
      hasApiKey: false,
      catalogReady: true,
      modelsCount: 0,
    });
    expect(issue?.actionTab).toBe("Store");
    expect(issue?.message).toMatch(/Store → Gateways/);
  });

  it("asks to connect the key after a gateway is installed", () => {
    const issue = duckyPickerIssue({
      gatewayCount: 1,
      hasApiKey: false,
      catalogReady: true,
      modelsCount: 0,
    });
    expect(issue?.actionTab).toBe("LLMs");
    expect(issue?.message).toMatch(/Connect your gateway/);
  });

  it("does not invent an issue once a key and models are live", () => {
    expect(
      duckyPickerIssue({
        gatewayCount: 1,
        hasApiKey: true,
        catalogReady: true,
        modelsCount: 3,
      }),
    ).toBeNull();
  });

  it("waits on an in-flight catalog instead of saying no models", () => {
    expect(
      duckyPickerIssue({
        gatewayCount: 1,
        hasApiKey: true,
        catalogReady: false,
        modelsCount: 0,
      }),
    ).toBeNull();
  });

  it("waits on plugin contributions before nagging about Store", () => {
    expect(
      duckyPickerIssue({
        gatewayCount: 0,
        contribReady: false,
        hasApiKey: false,
        catalogReady: false,
        modelsCount: 0,
      }),
    ).toBeNull();
  });
});
