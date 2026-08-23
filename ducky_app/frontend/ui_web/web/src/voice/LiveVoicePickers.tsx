import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { Icons } from "../icons/Icons";
import {
  rememberSettingsSections,
  requestOpenSettings,
} from "../navigation/openSettingsTab";
import { clampProcessTalk } from "./processNarration";
import { useTtsVoiceOptions } from "./pluginVoices";
import { SpeedDropdown } from "./SpeedDropdown";

export type LiveVoicePickersProps = {
  voiceId: string;
  speed: number;
  setVoiceId: (value: string) => void;
  setSpeed: (value: number) => void;
  manualSend?: boolean;
  setManualSend?: (value: boolean) => void;
  processTalk?: number;
  setProcessTalk?: (value: number) => void;
};

function openAudioIoSettings() {
  rememberSettingsSections({ audio: "input" });
  requestOpenSettings("Audio");
  window.dispatchEvent(
    new CustomEvent("ducky:settings-section", { detail: { tab: "Audio", section: "input" } }),
  );
}

/** Voice / speed / process-talk / auto-send — composer toolbar next to the agent picker. */
export function LiveVoicePickers({
  voiceId,
  speed,
  setVoiceId,
  setSpeed,
  manualSend = false,
  setManualSend,
  processTalk = 0.7,
  setProcessTalk,
}: LiveVoicePickersProps) {
  const voices = useTtsVoiceOptions();
  const talk = clampProcessTalk(processTalk);
  const talkPct = Math.round(talk * 100);

  return (
    <div className="live-voice-pickers">
      <div className="live-voice-combo">
        <ChoiceDropdown
          id="live-voice-picker"
          aria-label="Voice"
          mode="radio"
          size="compact"
          placement="top"
          minWidth={180}
          value={voiceId}
          options={[
            { value: "", label: "AI Voice default" },
            ...voices.map((v) => ({ value: v.id, label: v.label })),
          ]}
          onChange={setVoiceId}
        />
        <span className="live-voice-combo-split" aria-hidden />
        <SpeedDropdown
          id="live-voice-speed"
          aria-label="Talking speed"
          size="compact"
          placement="top"
          minWidth={160}
          value={speed || 1}
          onChange={(next) => setSpeed(next || 1)}
        />
      </div>
      {setProcessTalk ? (
        <label className="live-voice-process-talk" title="How much to narrate tools and thinking (0 = mute process talk)">
          <Icons.Settings />
          <input
            type="range"
            className="live-voice-process-talk-input"
            min={0}
            max={1}
            step={0.05}
            value={talk}
            aria-label="Process talk amount"
            onChange={(e) => setProcessTalk(clampProcessTalk(Number(e.target.value)))}
            style={{
              background: `linear-gradient(to right, var(--accent, #3b82f6) ${talkPct}%, color-mix(in srgb, var(--fg) 14%, transparent) ${talkPct}%)`,
            }}
          />
          <span className="live-voice-process-talk-value">{talkPct}%</span>
        </label>
      ) : null}
      {setManualSend ? (
        <button
          type="button"
          className={`voice-overlay-manual-toggle${manualSend ? " is-on" : ""}`}
          title={
            manualSend
              ? "Manual send on — talk as long as you want, then press Send"
              : "Auto-send on pause — click to wait for Send instead"
          }
          aria-pressed={manualSend}
          onClick={() => setManualSend(!manualSend)}
        >
          <Icons.Send />
        </button>
      ) : null}
      <button
        type="button"
        className="voice-btn voice-btn--tiny live-voice-io-settings"
        title="Input / Output settings"
        aria-label="Input / Output settings"
        onClick={openAudioIoSettings}
      >
        <Icons.Sliders />
      </button>
    </div>
  );
}
