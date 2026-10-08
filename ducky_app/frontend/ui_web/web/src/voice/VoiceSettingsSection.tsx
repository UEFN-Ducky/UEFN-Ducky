import { useEffect, useState } from "react";

import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { DuckyModelPicker } from "../components/ducky/DuckyModelPicker";
import { Icons } from "../icons/Icons";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { GeneralSectionHeader } from "../views/settings/GeneralSectionHeader";
import { SettingsToggleRow } from "../views/settings/SettingsToggleRow";
import { SpeedDropdown } from "./SpeedDropdown";
import { ttsEngine } from "./ttsEngine";
import { useTtsVoiceOptions } from "./pluginVoices";
import { windowsSpeechUsable } from "./transcriptionSession";
import {
  getVoiceSettings,
  loadVoiceSettings,
  normalizeSttProvider,
  saveVoiceSettings,
  type SpokenStyle,
  type SttProvider,
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
  const [processTalk, setProcessTalk] = useState(0.7);
  const [sttProvider, setSttProvider] = useState<SttProvider>("");
  const [autoSend, setAutoSend] = useState(false);
  const voices = useTtsVoiceOptions();
  const systemListen = windowsSpeechUsable() ? "Windows speech" : "Browser speech";

  useEffect(() => {
    void loadVoiceSettings().then((s) => {
      setEnabled(s.enabled);
      setStyle(s.spokenStyle);
      setModel(s.summaryModel);
      setVoice(s.defaultVoice);
      setSpeed(s.defaultSpeed);
      setProcessTalk(s.processTalk);
      setSttProvider(s.sttProvider);
      setAutoSend(s.liveAutoSend);
    });
    return subscribeVoiceSettings(() => {
      const s = getVoiceSettings();
      setEnabled(s.enabled);
      setStyle(s.spokenStyle);
      setModel(s.summaryModel);
      setVoice(s.defaultVoice);
      setSpeed(s.defaultSpeed);
      setProcessTalk(s.processTalk);
      setSttProvider(s.sttProvider);
      setAutoSend(s.liveAutoSend);
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
                { value: "", label: isRemote() ? "System speech (default)" : "Windows Speech (default)" },
                ...voices.map((v) => ({ value: v.id, label: v.label })),
              ]}
              onChange={(next) => {
                setVoice(next);
                void saveVoiceSettings({ defaultVoice: next });
                ttsEngine.setVoice(next);
              }}
            />
          </div>
          {!isRemote() ? (
            <button type="button" className="voice-notice-action"
              onClick={() => void getApi()?.voice_open_windows_settings?.("voice_download")}>
              Download Windows voices
            </button>
          ) : null}
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
          <p className="general-tab-section-desc">
            The mic button writes what you say into the chat box — you press Send. Live voice talks
            back; turn on Auto-send to send each pause as a turn. {systemListen} needs no key
            {systemListen === "Windows speech"
              ? " (Windows Settings → Privacy & security → Speech → Online speech recognition must be on)."
              : "."}
          </p>
          <div className="voice-settings-row">
            <label className="voice-settings-label" htmlFor="voice-stt-provider">
              Listen
            </label>
            <ChoiceDropdown
              id="voice-stt-provider"
              aria-label="Listen backend"
              mode="radio"
              value={sttProvider}
              options={[
                { value: "", label: systemListen, hint: "Free, no extra key" },
                { value: "openai", label: "OpenAI", hint: "Fastest live words — needs an OpenAI key" },
              ]}
              onChange={(next) => {
                const value = normalizeSttProvider(next);
                setSttProvider(value);
                void saveVoiceSettings({ sttProvider: value });
              }}
            />
          </div>
          <SettingsToggleRow
            id="toggle-voice-live-auto-send"
            label="Auto-send in live voice"
            description="Send what you said when you pause. Off: your words go into the chat box and you press Send."
            checked={autoSend}
            onChange={(value) => {
              setAutoSend(value);
              void saveVoiceSettings({ liveAutoSend: value });
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
            description="Read Ducky's answer aloud after you send a message. Only replies to your own messages speak — never background chats or automations. Stop any time with the ■ button by the mic."
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
