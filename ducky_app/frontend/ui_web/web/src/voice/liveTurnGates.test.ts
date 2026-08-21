import { describe, expect, it } from "vitest";

import {
  liveTtsBusy,
  shouldAcceptLiveFinal,
  shouldReturnToListeningAfterAnswer,
} from "./liveTurnGates";

describe("liveTurnGates", () => {
  it("rejects finals while TTS is busy (speaker echo)", () => {
    expect(shouldAcceptLiveFinal({ isSpeaking: true, queueLength: 0 })).toBe(false);
    expect(shouldAcceptLiveFinal({ isSpeaking: false, queueLength: 2 })).toBe(false);
    expect(liveTtsBusy(true, 0)).toBe(true);
  });

  it("accepts finals after barge-in cleared speech", () => {
    expect(shouldAcceptLiveFinal({ isSpeaking: false, queueLength: 0 })).toBe(true);
  });

  it("returns to listening when the answer finished and nothing else is queued", () => {
    expect(
      shouldReturnToListeningAfterAnswer({ speakingAfterAnswer: true, moreUtterancesQueued: false }),
    ).toBe(true);
    expect(
      shouldReturnToListeningAfterAnswer({ speakingAfterAnswer: true, moreUtterancesQueued: true }),
    ).toBe(false);
    expect(
      shouldReturnToListeningAfterAnswer({ speakingAfterAnswer: false, moreUtterancesQueued: false }),
    ).toBe(false);
  });
});
