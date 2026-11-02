import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";
import { ModelSelector } from "../components/ModelSelector";
import { Icons } from "../icons/Icons";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { getCachedModels } from "../hooks/modelsCatalogCache";
import {
  getAudioSettings,
  loadAudioSettings,
  saveAudioSettings,
  subscribeAudioSettings,
} from "./audioSettings";
import { getLiveVoiceChatIds } from "./liveChats";
import { listMicDevices, listOutputDevices } from "./micPermission";
import { clampProcessTalk } from "./processNarration";
import { useTtsVoiceOptions } from "./pluginVoices";
import { SpeedDropdown } from "./SpeedDropdown";
import { browserSpeechUsable, windowsSpeechUsable } from "./transcriptionSession";
import {
  getChatVoiceModel,
  getVoiceSettings,
  loadVoiceSettings,
  normalizeSttProvider,
  saveVoiceSettings,
  setChatVoiceModel,
  subscribeVoiceSettings,
  type SttProvider,
} from "./voiceSettings";

type CatalogLookup = Array<{ id: string; providerKey?: string }> | null;

/** The voice model is saved as "backend:model_id"; a bare id reads as unset on the backend
 *  (it fell back to the default model, so only Cursor's already-prefixed ids stuck).
 *  A coding agent's pick is "agent:id", a Ducky API pick "provider:id" from the catalog. */
export function qualifyVoiceModel(id: string, agent: string, catalog: CatalogLookup = getCachedModels()): string {
  const model = (id || "").trim();
  if (!model || model.includes(":")) return model;
  const by = (agent || "").trim();
  if (by && by !== "ducky") return `${by}:${model}`;
  const provider = catalog?.find((row) => row.id === model)?.providerKey?.trim();
  return provider ? `${provider}:${model}` : model;
}

/** The coding agent a saved voice model belongs to ("ducky" for an API provider). */
export function voiceModelAgent(saved: string, catalog: CatalogLookup = getCachedModels()): string {
  const at = (saved || "").indexOf(":");
  if (at <= 0) return "ducky";
  const prefix = saved.slice(0, at);
  return catalog?.some((row) => row.providerKey === prefix) ? "ducky" : prefix;
}

/** The chat the voice-model chip edits: the given one, else the only live chat. */
export function voiceModelChatId(chatId?: string): string {
  const given = (chatId || "").trim();
  if (given) return given;
  const live = [...getLiveVoiceChatIds()];
  return live.length === 1 ? live[0] : "";
}

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
  /** Live chat whose voice model the chip sets; without one it sets the global Live voice model. */
  chatId?: string;
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
  chatId,
}: LiveVoicePickersProps) {
  const modelChat = voiceModelChatId(chatId);
  const readVoiceModel = () => (modelChat ? getChatVoiceModel(modelChat) : "") || getVoiceSettings().liveModel;
  const [voiceModel, setVoiceModelState] = useState(readVoiceModel);
  // The picker names the agent first, then the model: keep it to qualify the saved id.
  const [voiceAgent, setVoiceAgentState] = useState(() => voiceModelAgent(readVoiceModel()));
  const voiceAgentRef = useRef(voiceAgent);
  const setVoiceAgent = useCallback((agent: string) => {
    voiceAgentRef.current = agent;
    setVoiceAgentState(agent);
  }, []);
  const voices = useTtsVoiceOptions();
  const talk = clampProcessTalk(processTalk);
  const talkPct = Math.round(talk * 100);
  const [sttProvider, setSttProvider] = useState<SttProvider>(() => getVoiceSettings().sttProvider);
  const [micDeviceId, setMicDeviceId] = useState(() => getAudioSettings().micDeviceId);
  const [outputDeviceId, setOutputDeviceId] = useState(() => getAudioSettings().outputDeviceId);
  const [micOptions, setMicOptions] = useState<ChoiceOption[]>([]);
  const [outputOptions, setOutputOptions] = useState<ChoiceOption[]>([]);
  const systemListen = windowsSpeechUsable() ? "Windows speech" : browserSpeechUsable() ? "Browser speech" : "";
  const [openaiKey, setOpenaiKey] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve(getApi()?.get_key_status?.())
      .then((status) => {
        if (!cancelled) setOpenaiKey(Boolean(status && (status as { openai?: boolean }).openai));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void loadVoiceSettings();
    void loadAudioSettings();
    return subscribeVoiceSettings(() => {
      setSttProvider(getVoiceSettings().sttProvider);
      const saved = readVoiceModel();
      setVoiceModelState(saved);
      setVoiceAgent(voiceModelAgent(saved));
    });
     
  }, [modelChat]);

  const setVoiceModel = useCallback(
    (picked: string) => {
      const next = qualifyVoiceModel(picked, voiceAgentRef.current);
      setVoiceModelState(next);
      if (modelChat) setChatVoiceModel(modelChat, next);
      else void saveVoiceSettings({ liveModel: next });
    },
    [modelChat],
  );

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
        label: systemListen ? `Listen: ${systemListen}` : "Listen: System (not available)",
        hint: systemListen === "Windows speech" ? "Free, no key — uses Windows online speech" : undefined,
        disabled: !systemListen,
      },
      {
        value: "openai",
        label: openaiKey === false ? "Listen: OpenAI (no key)" : "Listen: OpenAI",
        hint: openaiKey === false ? "Add an OpenAI key under Settings → LLMs" : "Fastest, most accurate live words",
      },
    ],
    [systemListen, openaiKey],
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
      <div className="live-voice-model live-voice-model--voice" title="Voice model: answers live talk right away">
        <ModelSelector
          selectedModel={voiceAgent === "ducky" ? voiceModel.slice(voiceModel.indexOf(":") + 1) : voiceModel}
          setSelectedModel={setVoiceModel}
          codingAgent={voiceAgent}
          setCodingAgent={setVoiceAgent}
          requireTools={false}
          preserveSelection
          menuPlacement="top"
          placeholder="Voice: chat's model"
          uiTarget="live-voice.voice-model"
        />
      </div>
      <ChoiceDropdown
        id="live-voice-listen"
        icon={<Icons.Headphones />}
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
          icon={<Icons.Speaker />}
          aria-label="Voice"
          mode="radio"
          size="compact"
          placement="top"
          minWidth={180}
          value={voiceId}
          options={[
            { value: "", label: isRemote() ? "System speech (default)" : "Windows Speech (default)" },
            ...voices.map((v) => ({ value: v.id, label: v.label })),
          ]}
          onChange={setVoiceId}
        />
        <span className="live-voice-combo-split" aria-hidden />
        <SpeedDropdown
          id="live-voice-speed"
          icon={<Icons.Clock />}
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
        icon={<Icons.Mic />}
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
        icon={<Icons.Headphones />}
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
