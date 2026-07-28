/**
 * Settings → Audio — Input / Output / AI Voice section tabs.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChoiceDropdown, type ChoiceOption } from "../../components/ChoiceDropdown";
import { Icons } from "../../icons/Icons";
import { previewSound } from "../../sfx/SoundFxBridge";
import { syncBuiltinOutputDevice } from "../../sfx/builtinSounds";
import { useAppearance } from "../../theme/AppearanceContext";
import {
  getAudioSettings,
  loadAudioSettings,
  saveAudioSettings,
  subscribeAudioSettings,
  type MicPermission,
} from "../../voice/audioSettings";
import { listMicDevices, listOutputDevices, openMicStream } from "../../voice/micPermission";
import { VoiceSettingsSection } from "../../voice/VoiceSettingsSection";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";

export type AudioSectionTab = "input" | "output" | "voice";

function permissionLabel(p: MicPermission): string {
  if (p === "allow") return "Allowed";
  if (p === "block") return "Blocked";
  return "Ask next time";
}

interface AudioTabProps {
  sectionTab?: AudioSectionTab;
}

export function AudioTab({ sectionTab = "input" }: AudioTabProps) {
  const { sounds, setSounds } = useAppearance();
  const [micPermission, setMicPermission] = useState<MicPermission>("ask");
  const [micDeviceId, setMicDeviceId] = useState("");
  const [outputDeviceId, setOutputDeviceId] = useState("");
  const [ttsVolume, setTtsVolume] = useState(1);
  const [audioMuted, setAudioMuted] = useState(false);
  const [micOptions, setMicOptions] = useState<ChoiceOption[]>([]);
  const [outputOptions, setOutputOptions] = useState<ChoiceOption[]>([]);
  const [testingMic, setTestingMic] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [micError, setMicError] = useState("");
  const testCleanupRef = useRef<(() => void) | null>(null);

  const refreshDevices = useCallback(async () => {
    try {
      const [mics, outs] = await Promise.all([listMicDevices(), listOutputDevices()]);
      setMicOptions([
        { value: "", label: `Windows default (${mics.defaultLabel})` },
        ...mics.devices.map((d) => ({ value: d.deviceId, label: d.label })),
      ]);
      setOutputOptions([
        { value: "", label: `Windows default (${outs.defaultLabel})` },
        ...outs.devices.map((d) => ({ value: d.deviceId, label: d.label })),
      ]);
    } catch {
      setMicOptions([{ value: "", label: "Windows default" }]);
      setOutputOptions([{ value: "", label: "Windows default" }]);
    }
  }, []);

  useEffect(() => {
    void loadAudioSettings().then((s) => {
      setMicPermission(s.micPermission);
      setMicDeviceId(s.micDeviceId);
      setOutputDeviceId(s.outputDeviceId);
      setTtsVolume(s.ttsVolume);
      setAudioMuted(s.audioMuted);
      void syncBuiltinOutputDevice();
    });
    return subscribeAudioSettings(() => {
      const s = getAudioSettings();
      setMicPermission(s.micPermission);
      setMicDeviceId(s.micDeviceId);
      setOutputDeviceId(s.outputDeviceId);
      setTtsVolume(s.ttsVolume);
      setAudioMuted(s.audioMuted);
    });
  }, []);

  useEffect(() => {
    if (sectionTab === "voice") return;
    void refreshDevices();
    const md = navigator.mediaDevices;
    if (!md?.addEventListener) return;
    const onChange = () => void refreshDevices();
    md.addEventListener("devicechange", onChange);
    return () => md.removeEventListener("devicechange", onChange);
  }, [micPermission, refreshDevices, sectionTab]);

  useEffect(() => {
    return () => {
      testCleanupRef.current?.();
      testCleanupRef.current = null;
    };
  }, []);

  // Keep picker values valid if a saved device disappeared.
  const micValue = useMemo(() => {
    if (!micDeviceId) return "";
    return micOptions.some((o) => o.value === micDeviceId) ? micDeviceId : "";
  }, [micDeviceId, micOptions]);
  const outputValue = useMemo(() => {
    if (!outputDeviceId) return "";
    return outputOptions.some((o) => o.value === outputDeviceId) ? outputDeviceId : "";
  }, [outputDeviceId, outputOptions]);

  const setPermission = (permission: MicPermission) => {
    setMicPermission(permission);
    void saveAudioSettings({ micPermission: permission });
    // Prime WebView2 so the host can persist Allow and the chrome dialog never returns.
    if (permission === "allow") {
      void openMicStream()
        .then((stream) => stream.getTracks().forEach((t) => t.stop()))
        .then(() => refreshDevices())
        .catch(() => undefined);
    }
  };

  const stopMicTest = useCallback(() => {
    testCleanupRef.current?.();
    testCleanupRef.current = null;
    setTestingMic(false);
    setMicLevel(0);
  }, []);

  useEffect(() => {
    if (sectionTab !== "input") stopMicTest();
  }, [sectionTab, stopMicTest]);

  const startMicTest = async () => {
    setMicError("");
    stopMicTest();
    if (micPermission === "block") {
      setMicError("Microphone is blocked. Click Allow first.");
      return;
    }
    try {
      // Settings test: if ask, treat as allow for this session after stream opens.
      if (micPermission === "ask") {
        await saveAudioSettings({ micPermission: "allow" });
      }
      const stream = await openMicStream();
      const ctx = new AudioContext();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      let raf = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const v = (data[i]! - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        setMicLevel(Math.min(1, rms * 4));
        raf = window.requestAnimationFrame(tick);
      };
      raf = window.requestAnimationFrame(tick);
      setTestingMic(true);
      void refreshDevices();
      testCleanupRef.current = () => {
        window.cancelAnimationFrame(raf);
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close().catch(() => undefined);
      };
      window.setTimeout(() => stopMicTest(), 8000);
    } catch (err) {
      setMicError(
        err instanceof Error
          ? err.message
          : "Could not open microphone. Check Windows mic privacy for UEFN Ducky.",
      );
      setTestingMic(false);
    }
  };

  if (sectionTab === "voice") {
    return (
      <div className="general-tab-shell">
        <h2 className="general-tab-page-title">AI Voice</h2>
        <VoiceSettingsSection />
      </div>
    );
  }

  if (sectionTab === "output") {
    return (
      <div className="general-tab-shell">
        <h2 className="general-tab-page-title">Output</h2>
        <section className="general-tab-section">
          <GeneralSectionHeader icon={<Icons.Speaker />} title="Playback" />
          <div className="general-tab-toggle-card">
            <div className="voice-settings-row">
              <span className="voice-settings-label" id="audio-output-device-label">
                Output device
              </span>
              <ChoiceDropdown
                id="audio-output-device"
                aria-label="Output device"
                mode="radio"
                value={outputValue}
                options={outputOptions}
                minWidth={280}
                onChange={(next) => {
                  setOutputDeviceId(next);
                  void saveAudioSettings({ outputDeviceId: next }).then(() => syncBuiltinOutputDevice());
                }}
              />
            </div>
            <SettingsToggleRow
              id="toggle-audio-mute"
              label="Mute all audio"
              description="Silence sound effects and spoken replies."
              checked={audioMuted}
              onChange={(value) => {
                setAudioMuted(value);
                void saveAudioSettings({ audioMuted: value });
              }}
            />
            <label className="appearance-sounds-row">
              <span>Enable sound effects</span>
              <input
                type="checkbox"
                checked={sounds.enabled}
                onChange={(e) => void setSounds({ ...sounds, enabled: e.target.checked })}
              />
            </label>
            <label className="appearance-sounds-row">
              <span>SFX volume</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={sounds.volume}
                disabled={audioMuted || !sounds.enabled}
                onChange={(e) => void setSounds({ ...sounds, volume: Number(e.target.value) })}
              />
              <span className="appearance-sounds-vol">{Math.round(sounds.volume * 100)}%</span>
            </label>
            <label className="appearance-sounds-row">
              <span>TTS volume</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={ttsVolume}
                disabled={audioMuted}
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setTtsVolume(next);
                  void saveAudioSettings({ ttsVolume: next });
                }}
              />
              <span className="appearance-sounds-vol">{Math.round(ttsVolume * 100)}%</span>
            </label>
            <div className="audio-tab-btn-row">
              <button
                type="button"
                className="settings-btn"
                onClick={() => previewSound("builtin:ding", sounds.volume || 0.5)}
              >
                Test speaker
              </button>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="general-tab-shell">
      <h2 className="general-tab-page-title">Input</h2>
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Mic />} title="Microphone" />
        <div className="general-tab-toggle-card">
          <div className="audio-tab-status-row">
            <span className="audio-tab-status-label">Permission</span>
            <span className={`audio-tab-status-value audio-tab-status-value--${micPermission}`}>
              {permissionLabel(micPermission)}
            </span>
          </div>
          <div className="audio-tab-btn-row">
            <button
              type="button"
              className="settings-btn general-tab-btn-primary"
              onClick={() => setPermission("allow")}
            >
              Allow
            </button>
            <button type="button" className="settings-btn" onClick={() => setPermission("block")}>
              Block
            </button>
            <button type="button" className="settings-btn" onClick={() => setPermission("ask")}>
              Reset to ask
            </button>
          </div>
          <div className="voice-settings-row">
            <span className="voice-settings-label" id="audio-input-device-label">
              Input device
            </span>
            <ChoiceDropdown
              id="audio-input-device"
              aria-label="Input device"
              mode="radio"
              value={micValue}
              options={micOptions}
              disabled={micPermission === "block"}
              minWidth={280}
              onChange={(next) => {
                setMicDeviceId(next);
                void saveAudioSettings({ micDeviceId: next });
              }}
            />
          </div>
          <div className="audio-tab-test-row">
            <button
              type="button"
              className="settings-btn"
              disabled={micPermission === "block" || testingMic}
              onClick={() => void startMicTest()}
            >
              {testingMic ? "Listening…" : "Test microphone"}
            </button>
            {testingMic ? (
              <button type="button" className="settings-btn" onClick={stopMicTest}>
                Stop
              </button>
            ) : null}
            <div className="audio-tab-level" aria-hidden>
              <div className="audio-tab-level-fill" style={{ width: `${Math.round(micLevel * 100)}%` }} />
            </div>
          </div>
          {micError ? <p className="audio-tab-error">{micError}</p> : null}
        </div>
      </section>
    </div>
  );
}
