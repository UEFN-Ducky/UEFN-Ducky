/**
 * Settings → Videos — video attachment limits and the on-demand ffmpeg install.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import type { FfmpegStatusDto, VideoSettingsDto } from "../../types/panel";
import { AppNotice } from "../../components/AppNotice";
import { Icons } from "../../icons/Icons";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

type NumericKey = "video_max_mb" | "video_frames_per_video" | "max_images_per_message";

const FIELDS: { key: NumericKey; label: string; min: number; max: number; hint: string; auto?: number }[] = [
  { key: "video_max_mb", label: "Max video size (MB)", min: 10, max: 200, hint: "Largest video one message can carry." },
  { key: "video_frames_per_video", label: "Frames per video", min: 1, max: 40, auto: 20, hint: "Frames sent to models without native video (Claude, OpenAI, coding agents…)." },
  { key: "max_images_per_message", label: "Max images per message", min: 1, max: 100, auto: 40, hint: "Images per message, video frames included." },
];

function ffmpegLabel(st: FfmpegStatusDto): string {
  if (st.state === "ready" && st.bundled) return `Included with UEFN-Ducky (${st.version})`;
  if (st.state === "ready") return `Installed (${st.version})`;
  if (st.state === "installing") return `Installing… ${Math.round(st.progress * 100)}%`;
  if (st.state === "error") return st.error || "Install failed";
  return "Not installed";
}

function errMsg(prefix: string, err: unknown): string {
  return `${prefix}: ${err instanceof Error ? err.message : String(err)}`;
}

export function VideosTab() {
  const [settings, setSettings] = useState<VideoSettingsDto | null>(null);
  const [draft, setDraft] = useState<Record<NumericKey, string>>({
    video_max_mb: "", video_frames_per_video: "", max_images_per_message: "",
  });

  const [error, setError] = useState("");
  const pollInFlight = useRef(false);

  const apply = useCallback((s: VideoSettingsDto) => {
    setSettings(s);
    setDraft({
      video_max_mb: String(s.video_max_mb),
      video_frames_per_video: String(s.video_frames_per_video),
      max_images_per_message: String(s.max_images_per_message),
    });
  }, []);

  const load = useCallback(async () => {
    try {
      const s = await getApi()?.get_video_settings?.();
      if (s) {
        apply(s);
        setError("");
      }
    } catch (err) {
      setError(errMsg("Failed to load video settings", err));
    }
  }, [apply]);

  useEffect(() => {
    void load();
  }, [load]);

  const installing = settings?.ffmpeg.state === "installing";
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => {
      if (pollInFlight.current) return;
      pollInFlight.current = true;
      void (async () => {
        try {
          const ffmpeg = await getApi()?.get_ffmpeg_status?.();
          if (ffmpeg) setSettings((prev) => (prev ? { ...prev, ffmpeg } : prev));
        } catch (err) {
          setError(errMsg("Failed to read ffmpeg status", err));
        } finally {
          pollInFlight.current = false;
        }
      })();
    }, 500);
    return () => window.clearInterval(timer);
  }, [installing]);

  const save = useCallback(async (key: NumericKey) => {
    if (!settings) return;
    const raw = draft[key].trim();
    const rawValue = Number(raw);
    if (raw === "" || !Number.isFinite(rawValue)) {
      setDraft((d) => ({ ...d, [key]: String(settings[key]) }));
      return;
    }
    const field = FIELDS.find((f) => f.key === key);
    const value = field ? Math.max(field.min, rawValue) : rawValue;
    if (value === settings[key]) return;
    try {
      const next = await getApi()?.set_video_settings?.({ [key]: value });
      if (next) {
        apply(next);
        setError("");
      }
    } catch (err) {
      setError(errMsg("Failed to save video settings", err));
    }
  }, [draft, settings, apply]);

  const lastManual = useRef<Partial<Record<NumericKey, number>>>({});

  const toggleAuto = useCallback(async (key: NumericKey, defaultManual: number, on: boolean) => {
    if (!settings) return;
    if (on) {
      if (settings[key] > 0) lastManual.current[key] = settings[key];
    }
    const value = on ? 0 : lastManual.current[key] ?? defaultManual;
    try {
      const next = await getApi()?.set_video_settings?.({ [key]: value });
      if (next) {
        apply(next);
        setError("");
      }
    } catch (err) {
      setError(errMsg("Failed to save video settings", err));
    }
  }, [settings, apply]);

  const runFfmpeg = useCallback(async (action: "install" | "remove") => {
    try {
      const api = getApi();
      const ffmpeg = action === "install" ? await api?.install_ffmpeg?.() : await api?.remove_ffmpeg?.();
      if (ffmpeg) setSettings((prev) => (prev ? { ...prev, ffmpeg } : prev));
      setError("");
    } catch (err) {
      setError(errMsg(`Failed to ${action} ffmpeg`, err));
    }
  }, []);

  if (!settings) {
    return error ? (
      <div className="general-tab-shell videos-tab">
        <AppNotice message={error} className="plans-tab-notice" />
        <div className="videos-tab-actions">
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </div>
      </div>
    ) : null;
  }
  return (
    <div className="general-tab-shell videos-tab">
      {error ? <AppNotice message={error} className="plans-tab-notice" /> : null}
      <GeneralSectionHeader icon={<Icons.Play />} title="Videos" />
      {FIELDS.map((f) => (
        <div key={f.key} className="memory-tab-field">
          <span className="memory-tab-field-label">{f.label}</span>
          <input
            className="memory-tab-input"
            type="number"
            min={f.min}
            max={f.max}
            aria-label={f.label}
            value={f.auto !== undefined && settings[f.key] === 0 ? "" : draft[f.key]}
            placeholder={f.auto !== undefined && settings[f.key] === 0 ? "Auto" : undefined}
            disabled={f.auto !== undefined && settings[f.key] === 0}
            onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
            onBlur={() => void save(f.key)}
          />
          {f.auto !== undefined ? (
            <label className="memory-tab-field-hint">
              <input
                type="checkbox"
                aria-label={`${f.label} Auto`}
                checked={settings[f.key] === 0}
                onChange={(e) => void toggleAuto(f.key, f.auto as number, e.target.checked)}
              />{" "}
              Auto
            </label>
          ) : null}
          <span className="memory-tab-field-hint">{f.hint}</span>
        </div>
      ))}
      <div className="memory-tab-field">
        <span className="memory-tab-field-label">ffmpeg</span>
        <span>{ffmpegLabel(settings.ffmpeg)}</span>
        {settings.ffmpeg.bundled ? null : (
        <span className="memory-tab-field-hint">
          Downloaded automatically (~67 MB, LGPL build) the first time a video needs frames. Gemini users with small
          videos never need it.
        </span>
        )}
        {settings.ffmpeg.bundled ? null : (
        <div className="videos-tab-actions">
          {settings.ffmpeg.state !== "ready" ? (
            <button type="button" disabled={installing} onClick={() => void runFfmpeg("install")}>
              Install now
            </button>
          ) : (
            <button type="button" onClick={() => void runFfmpeg("remove")}>
              Remove
            </button>
          )}
        </div>
        )}
      </div>
    </div>
  );
}
