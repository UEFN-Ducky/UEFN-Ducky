// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  get_settings: vi.fn(async () => ({ audio_muted: true, tts_volume: 1 })),
  save_agent_settings: vi.fn(async () => ({ ok: true })),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));

import { loadAudioSettings } from "./audioSettings";
import { ttsEngine } from "./ttsEngine";
import { SpeakMessageButton } from "./VoiceControls";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  ttsEngine.cancel();
  vi.clearAllMocks();
});

describe("Read this reply aloud while Ducky's audio is muted", () => {
  it("says it is muted instead of doing nothing, and Unmute turns sound back on", async () => {
    await loadAudioSettings();
    render(<SpeakMessageButton text="Wave 1 is closed." />);

    fireEvent.click(screen.getByLabelText("Read reply aloud"));
    expect(await screen.findByText("Ducky's audio is muted.")).toBeTruthy();

    const speak = vi.spyOn(ttsEngine, "speak").mockImplementation(() => undefined);
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Unmute Ducky"));
    });
    expect(api.save_agent_settings).toHaveBeenCalledWith(expect.objectContaining({ audio_muted: false }));
    expect(speak).toHaveBeenCalledWith("Wave 1 is closed.", expect.any(String), expect.any(Number));
  });

  it("only the reply that was pressed shows the notice", async () => {
    await loadAudioSettings();
    render(
      <>
        <SpeakMessageButton text="First reply." />
        <SpeakMessageButton text="Second reply." />
      </>,
    );
    fireEvent.click(screen.getAllByLabelText("Read reply aloud")[1]!);
    expect(await screen.findAllByText("Ducky's audio is muted.")).toHaveLength(1);
  });
});
