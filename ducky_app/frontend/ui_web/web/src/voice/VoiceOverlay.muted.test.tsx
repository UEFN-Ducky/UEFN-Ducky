// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  get_settings: vi.fn(async () => ({ audio_muted: true, tts_volume: 1 })),
  save_agent_settings: vi.fn(async () => ({ ok: true })),
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("./pluginVoices", () => ({ useTtsVoiceOptions: () => [] }));

import { loadAudioSettings } from "./audioSettings";
import { VoiceOverlay } from "./VoiceOverlay";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Live voice panel while Ducky's audio is muted", () => {
  it("says replies won't play and offers Unmute", async () => {
    await loadAudioSettings();
    render(
      <VoiceOverlay chatId="voice" open onClose={vi.fn()} onBack={vi.fn()} onForward={vi.fn()} onNewest={vi.fn()} showPickers={false} />,
    );
    expect(screen.getByText(/audio is muted, so replies won.t play/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Unmute Ducky"));
    });
    expect(api.save_agent_settings).toHaveBeenCalledWith(expect.objectContaining({ audio_muted: false }));
    expect(screen.queryByText(/audio is muted/)).toBeNull();
  });
});
