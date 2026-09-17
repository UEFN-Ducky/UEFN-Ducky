// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./ModelSelector", () => ({
  ModelSelector: (p: { selectedModel: string }) => (
    <button type="button" data-testid="interrupted-model-picker">
      {p.selectedModel}
    </button>
  ),
}));
vi.mock("./rich-content/RichContentRenderer", () => ({
  RichContentRenderer: (p: { text: string }) => <div>{p.text}</div>,
}));
vi.mock("./ThinkingBlock", () => ({ ThinkingBlock: () => null }));
vi.mock("../voice/VoiceControls", () => ({ SpeakMessageButton: () => null }));
vi.mock("../voice/TtsReadAlong", () => ({ TtsReadAlong: () => null, mapReadAlong: () => null }));
vi.mock("../voice/ttsEngine", () => ({
  ttsEngine: {
    onProgress: () => () => {},
    getProgress: () => ({ state: "idle", sourceText: "", spokenText: "", charIndex: 0, loading: false }),
  },
}));

import { MessageBubble } from "./MessageBubble";

describe("MessageBubble interrupted continue", () => {
  afterEach(() => cleanup());

  it("puts the model picker next to Continue", () => {
    render(
      <MessageBubble
        role="assistant"
        text="partial"
        incomplete
        error="You've hit your session limit"
        onContinue={() => {}}
        selectedModel="claude-sonnet-5"
        setSelectedModel={() => {}}
      />,
    );
    expect(screen.getByText(/Interrupted: You've hit your session limit/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue" })).toBeTruthy();
    expect(screen.getByTestId("interrupted-model-picker").textContent).toBe("claude-sonnet-5");
  });
});
