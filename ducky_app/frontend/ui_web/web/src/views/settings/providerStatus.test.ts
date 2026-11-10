import { expect, it } from "vitest";
import { connectedViaAgent } from "./providerStatus";

it("a signed-in Gemini CLI turns the Google (gemini) provider green", () => {
  const ok = { google: true, anthropic: true };
  expect(connectedViaAgent({ id: "gemini", plugin_id: "google" }, ok)).toBe(true);
  expect(connectedViaAgent({ id: "anthropic", plugin_id: "anthropic" }, ok)).toBe(true);
  expect(connectedViaAgent({ id: "openai", plugin_id: "openai" }, ok)).toBe(false);
  expect(connectedViaAgent({ id: "cursor" }, { cursor: true })).toBe(true);
});
