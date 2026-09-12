import { useCallback, useEffect, useMemo, useState } from "react";

import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";
import { ModelSelector } from "../components/ModelSelector";
import { Icons } from "../icons/Icons";
import { isRemote } from "../hooks/usePanelApi";
import {
  getAudioSettings,
  loadAudioSettings,
  saveAudioSettings,
  subscribeAudioSettings,
} from "./audioSettings";
import { listMicDevices, listOutputDevices } from "./micPermission";
import { clampProcessTalk } from "./processNarration";
import { useTtsVoiceOptions } from "./pluginVoices";
import { SpeedDropdown } from "./SpeedDropdown";
import { getSpeechRecognitionCtor } from "./transcriptionSession";
import {
  getVoiceSettings,
  loadVoiceSettings,
  normalizeSttProvider,
  saveVoiceSettings,
  subscribeVoiceSettings,
  type SttProvider,
} from "./voiceSettings";

export type LiveVoicePickersProps = {
  voiceId: string;
  speed: number;
  setVoiceId: (value: string) => void;
  setSpeed: (value: number) => void;
  processTalk?: number;
  setProcessTalk?: (value: number) => void;
  chatModel?: string;
  setChatModel?: (value: string) => void;
  codingAgent?: string;
  setCodingAgent?: (value: string) => void;
  showChatModel?: boolean;
};

export function LiveVoicePickers({
  voiceId,
  speed,
  setVoiceId,
  setSpeed,
  processTalk = 0.7,
  setProcessTalk,
  chatModel = "",
  setChatModel,
  codingAgent = "ducky",
  setCodingAgent,
  showChatModel = false,
}: LiveVoicePickersProps) {
  const voices = useTtsVoiceOptions();
  const talk = clampProcessTalk(processTalk);
  const talkPct = Math.round(talk * 100);
  const [sttProvider, setSttProvider] = useState<SttProvider>(() => getVoiceSettings().sttProvider);
  const [micDeviceId, setMicDeviceId] = useState(() => getAudioSettings().micDeviceId);
  const [outputDeviceId, setOutputDeviceId] = useState(() => getAudioSettings().outputDeviceId);
  const [micOptions, setMicOptions] = useState<ChoiceOption[]>([]);
  const [outputOptions, setOutputOptions] = useState<ChoiceOption[]>([]);
  const speechOk = Boolean(getSpeechRecognitionCtor());

  useEffect(() => {
    void loadVoiceSettings();
    void loadAudioSettings();
    return subscribeVoiceSettings(() => setSttProvider(getVoiceSettings().sttProvider));
  }, []);

  useEffect(() => subscribeAudioSettings(() => {
    const s = getAudioSettings();
    setMicDeviceId(s.micDeviceId);
    setOutputDeviceId(s.outputDeviceId);
  }), []);

  const refreshDevices = useCallback(async () => {
    try {
      const [mics, outs] = await Promise.all([listMicDevices(), listOutputDevices()]);
      const fallback = isRemote() ? "This device" : "Windows default";
      setMicOptions([
        { value: "", label: `${fallback} (${mics.defaultLabel})` },
        ...mics.devices.map((d) => ({ value: d.deviceId, label: d.label })),
      ]);
      setOutputOptions([
        { value: "", label: `${fallback} (${outs.defaultLabel})` },
        ...outs.devices.map((d) => ({ value: d.deviceId, label: d.label })),
      ]);
    } catch {
      const fallback = isRemote() ? "This device" : "Windows default";
      setMicOptions([{ value: "", label: fallback }]);
      setOutputOptions([{ value: "", label: fallback }]);
    }
  }, []);

  useEffect(() => {
    void refreshDevices();
    const md = navigator.mediaDevices;
    if (!md?.addEventListener) return;
    const onChange = () => void refreshDevices();
    md.addEventListener("devicechange", onChange);
    return () => md.removeEventListener("devicechange", onChange);
  }, [refreshDevices]);

  const listenOptions = useMemo<ChoiceOption[]>(
    () => [
      {
        value: "",
        label: "Listen: System default",
        disabled: !speechOk,
      },
      { value: "openai", label: "Listen: OpenAI" },
    ],
    [speechOk],
  );

  const micValue = micOptions.some((o) => o.value === micDeviceId) ? micDeviceId : "";
  const outputValue = outputOptions.some((o) => o.value === outputDeviceId) ? outputDeviceId : "";

  return (
    <div className="live-voice-pickers">
      {showChatModel && setChatModel ? (
        <div className="live-voice-model">
          <ModelSelector
            selectedModel={chatModel}
            setSelectedModel={setChatModel}
            codingAgent={codingAgent}
            setCodingAgent={setCodingAgent}
            preserveSelection
            menuPlacement="top"
            placeholder="Default model"
          />
        </div>
      ) : null}
      <ChoiceDropdown
        id="live-voice-listen"
        aria-label="Listen backend"
        mode="radio"
        size="compact"
        placement="top"
        minWidth={200}
        value={sttProvider}
        options={listenOptions}
        onChange={(next) => {
          const value = normalizeSttProvider(next);
          setSttProvider(value);
          void saveVoiceSettings({ sttProvider: value });
        }}
      />
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
      <ChoiceDropdown
        id="live-voice-mic"
        aria-label="Microphone"
        mode="radio"
        size="compact"
        placement="top"
        minWidth={200}
        value={micValue}
        options={micOptions.length ? micOptions : [{ value: "", label: "Microphone" }]}
        onChange={(next) => {
          setMicDeviceId(next);
          void saveAudioSettings({ micDeviceId: next });
        }}
      />
      <ChoiceDropdown
        id="live-voice-output"
        aria-label="Speakers"
        mode="radio"
        size="compact"
        placement="top"
        minWidth={200}
        value={outputValue}
        options={outputOptions.length ? outputOptions : [{ value: "", label: "Speakers" }]}
        onChange={(next) => {
          setOutputDeviceId(next);
          void saveAudioSettings({ outputDeviceId: next });
        }}
      />
    </div>
  );
}
