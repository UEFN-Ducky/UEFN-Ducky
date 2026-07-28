import { describe, expect, it } from "vitest";
import { matchDuckyProfileId } from "./openDuckyEditor";

describe("matchDuckyProfileId", () => {
  const profiles = [
    { id: "audio", name: "Audio" },
    { id: "producer", name: "Producer" },
    { id: "audio-2", name: "Audio" },
  ];

  it("prefers stored profileId over display name", () => {
    expect(
      matchDuckyProfileId({ profileId: "audio-2", duckyName: "Audio", name: "Chat 1" }, profiles),
    ).toBe("audio-2");
  });

  it("matches duckyName case-insensitively when unique", () => {
    expect(matchDuckyProfileId({ duckyName: "producer", name: "Chat 1" }, profiles)).toBe(
      "producer",
    );
  });

  it("falls back to chat name when unique", () => {
    expect(matchDuckyProfileId({ name: "Producer" }, profiles)).toBe("producer");
  });

  it("returns null when display name is ambiguous (duplicate agents)", () => {
    expect(matchDuckyProfileId({ duckyName: "Audio" }, profiles)).toBeNull();
  });

  it("returns null when nothing matches", () => {
    expect(matchDuckyProfileId({ name: "Unknown" }, profiles)).toBeNull();
  });

  it("ignores unknown profileId and falls back to unique name", () => {
    expect(matchDuckyProfileId({ profileId: "gone", name: "Producer" }, profiles)).toBe(
      "producer",
    );
  });
});
