import { useEffect, useState } from "react";

import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { DuckyModelPicker } from "../components/ducky/DuckyModelPicker";
import { Icons } from "../icons/Icons";
import { GeneralSectionHeader } from "../views/settings/GeneralSectionHeader";
import { SettingsToggleRow } from "../views/settings/SettingsToggleRow";
import { SpeedDropdown } from "./SpeedDropdown";
import { ttsEngine } from "./ttsEngine";
import { useTtsVoiceOptions } from "./pluginVoices";
import {
  getVoiceSettings,
  loadVoiceSettings,
  saveVoiceSettings,
  type SpokenStyle,
  subscribeVoiceSettings,
} from "./voiceSettings";

/**
 * Settings → Audio → AI Voice.
 * Defaults + live-voice prefs always editable; spoken-replies toggle is separate
 * and does not gate mic / live voice / these pickers.
 */
export function VoiceSettingsSection() {
  const [enabled, setEnabled] = useState(false);
  const [style, setStyle] = useState<SpokenStyle>("summary");
  const [model, setModel] = useState("");
  const [voice, setVoice] = useState("");
  const [speed, setSpeed] = useState(1);
  const [liveManualSend, setLiveManualSend] = useState(false);
  const [processTalk, setProcessTalk] = useState(0.7);
  const voices = useTtsVoiceOptions();

  useEffect(() => {
    void loadVoiceSettings().then((s) => {
      setEnabled(s.enabled);
      setStyle(s.spokenStyle);
      setModel(s.summaryModel);
      setVoice(s.defaultVoice);
      setSpeed(s.defaultSpeed);
      setLiveManualSend(s.liveManualSend);
      setProcessTalk(s.processTalk);
    });
    return subscribeVoiceSettings(() => {
      const s = getVoiceSettings();
      setEnabled(s.enabled);
      setStyle(s.spokenStyle);
      setModel(s.summaryModel);
      setVoice(s.defaultVoice);
      setSpeed(s.defaultSpeed);
      setLiveManualSend(s.liveManualSend);
      setProcessTalk(s.processTalk);
    });
  }, []);

  return (
    <>
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Speaker />} title="Defaults" />
        <p className="general-tab-section-desc">
          Global AI voice defaults. A ducky&apos;s Voice picker &quot;AI Voice default&quot; uses
          these when it has no override.
        </p>
        <div className="general-tab-toggle-card">
          <div className="voice-settings-row">
            <label className="voice-settings-label" htmlFor="voice-spoken-style">
              Spoken style
            </label>
            <ChoiceDropdown
              id="voice-spoken-style"
              aria-label="Spoken style"
              mode="radio"
              value={style}
              options={[
                { value: "summary", label: "Short summary after reply" },
                { value: "speak_along", label: "Speak along while typing" },
              ]}
              onChange={(next) => {
                const styleNext: SpokenStyle = next === "speak_along" ? "speak_along" : "summary";
                setStyle(styleNext);
                void saveVoiceSettings({ spokenStyle: styleNext });
              }}
            />
          </div>
          <div className="voice-settings-row">
            <label className="voice-settings-label">Voice summary model</label>
            <DuckyModelPicker
              model={model}
              onChange={(next) => {
                setModel(next);
                void saveVoiceSettings({ summaryModel: next });
              }}
              allowClear
              requireTools={false}
              placeholder="Default model"
              hint="Cheap model for spoken summaries only. Leave empty for Default Model."
            />
          </div>
          <div className="voice-settings-row">
            <label className="voice-settings-label" htmlFor="voice-default-voice">
              Default voice
            </label>
            <ChoiceDropdown
              id="voice-default-voice"
              aria-label="Default voice"
              mode="radio"
              value={voice}
              options={[
                { value: "", label: "System default" },
                ...voices.map((v) => ({ value: v.id, label: v.label })),
              ]}
              onChange={(next) => {
                setVoice(next);
                void saveVoiceSettings({ defaultVoice: next });
                ttsEngine.setVoice(next);
              }}
            />
          </div>
          <div className="voice-settings-row">
            <label className="voice-settings-label" htmlFor="voice-default-speed">
              Talking speed
            </label>
            <SpeedDropdown
              id="voice-default-speed"
              aria-label="Talking speed"
              value={speed || 1}
              onChange={(value) => {
                const next = value || 1;
                setSpeed(next);
                void saveVoiceSettings({ defaultSpeed: next });
                ttsEngine.setRate(next);
              }}
            />
          </div>
        </div>
      </section>

      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Mic />} title="Live Voice" />
        <div className="general-tab-toggle-card">
          <SettingsToggleRow
            id="toggle-voice-live-manual-send"
            label="Wait for Send"
            description="Keep listening through pauses; send only when you press Send."
            checked={liveManualSend}
            onChange={(value) => {
              setLiveManualSend(value);
              void saveVoiceSettings({ liveManualSend: value });
            }}
          />
          <div className="voice-settings-row">
            <label className="voice-settings-label" htmlFor="voice-process-talk">
              Process talk
            </label>
            <p className="general-tab-section-desc">
              How much to narrate tools and thinking during live chat. Final answers still speak.
            </p>
            <div className="live-voice-process-talk live-voice-process-talk--settings">
              <input
                id="voice-process-talk"
                type="range"
                className="live-voice-process-talk-input"
                min={0}
                max={1}
                step={0.05}
                value={processTalk}
                aria-label="Process talk amount"
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setProcessTalk(next);
                  void saveVoiceSettings({ processTalk: next });
                }}
              />
              <span className="live-voice-process-talk-value">
                {processTalk <= 0 ? "Off" : `${Math.round(processTalk * 100)}%`}
              </span>
            </div>
          </div>
        </div>
      </section>

      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Speaker />} title="Spoken replies" />
        <div className="general-tab-toggle-card">
          <SettingsToggleRow
            id="toggle-voice-enable"
            label="Enable spoken replies"
            description="Auto-speak after normal (typed) chat replies. Does not turn off live voice, dictation, or the defaults above."
            checked={enabled}
            onChange={(value) => {
              setEnabled(value);
              void saveVoiceSettings({ enabled: value });
            }}
          />
        </div>
      </section>
    </>
  );
}
