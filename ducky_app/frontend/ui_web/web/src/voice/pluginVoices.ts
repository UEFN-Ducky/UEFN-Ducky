/**
 * Voice-option assembly for the pickers: builtin system voices + plugin voices.
 *
 * Plugin voices come from two places, both keyed `plugin:<pluginId>:<voiceId>`:
 *  - static  — declared in a plugin manifest `tts.voices` (always present when enabled)
 *  - dynamic — fetched at runtime from the plugin backend (e.g. an ElevenLabs
 *              account's cloned voices), via plugin_tts_voices_start/poll.
 */

import { useEffect, useState } from "react";

import { getApi } from "../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { ttsEngine, type TtsVoiceInfo } from "./ttsEngine";

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function dedupeById(voices: TtsVoiceInfo[]): TtsVoiceInfo[] {
  const seen = new Set<string>();
  const out: TtsVoiceInfo[] = [];
  for (const v of voices) {
    if (seen.has(v.id)) continue;
    seen.add(v.id);
    out.push(v);
  }
  return out;
}

function staticPluginVoices(
  contribVoices: { id: string; label?: string; plugin_id: string }[],
): TtsVoiceInfo[] {
  return contribVoices.map((v) => ({
    id: `plugin:${v.plugin_id}:${v.id}`,
    label: `${v.label || v.id} (${v.plugin_id})`,
    kind: "plugin" as const,
    pluginId: v.plugin_id,
    voiceId: v.id,
  }));
}

/** Poll a plugin's dynamic voice list (returns [] on any failure / no lister). */
export async function loadPluginDynamicVoices(pluginId: string): Promise<TtsVoiceInfo[]> {
  const api = getApi();
  if (!api?.plugin_tts_voices_start || !api?.plugin_tts_voices_poll) return [];
  try {
    const started = await api.plugin_tts_voices_start(pluginId);
    if (!started?.ok || !started.job_id) return [];
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      await sleep(150);
      const polled = await api.plugin_tts_voices_poll(String(started.job_id));
      if (polled?.pending) continue;
      if (!polled?.ok) return [];
      return (polled.voices || [])
        .filter((v) => v && typeof v.id === "string" && v.id.length > 0)
        .map((v) => ({
          id: `plugin:${pluginId}:${v.id}`,
          label: `${v.label || v.id} (${pluginId})`,
          kind: "plugin" as const,
          pluginId,
          voiceId: v.id,
        }));
    }
    return [];
  } catch {
    return [];
  }
}

/**
 * Merged voice options for a picker dropdown: builtin voices first, then plugin
 * voices (static manifest voices, then dynamically-fetched ones). De-duplicated by
 * id, so a premade voice that also appears in an account fetch is listed once.
 *
 * Re-runs when the enabled plugins / their static voices change (a secret save
 * pushes `uefn_plugins_changed`, which refreshes the contribution registry).
 */
export function useTtsVoiceOptions(): TtsVoiceInfo[] {
  const contrib = usePluginContributions();
  const [voices, setVoices] = useState<TtsVoiceInfo[]>([]);
  // Bumped on uefn_plugins_changed so saving an API key re-fetches account voices,
  // even though the static/dynamic keys below stay identical across that event.
  const [refreshNonce, setRefreshNonce] = useState(0);

  const staticKey = contrib.tts_voices.map((v) => `${v.plugin_id}:${v.id}`).join("|");
  const dynamicKey = contrib.tts_voice_plugins.join("|");

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "uefn_plugins_changed") setRefreshNonce((n) => n + 1);
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const builtin = await ttsEngine.whenVoicesReady();
      const staticVoices = staticPluginVoices(contrib.tts_voices);
      // Show builtin + static immediately; dynamic voices land when the fetch returns.
      if (!cancelled) setVoices(dedupeById([...builtin, ...staticVoices]));
      if (!contrib.tts_voice_plugins.length) return;
      const dynamic = (
        await Promise.all(contrib.tts_voice_plugins.map((pid) => loadPluginDynamicVoices(pid)))
      ).flat();
      if (!cancelled) setVoices(dedupeById([...builtin, ...staticVoices, ...dynamic]));
    })();
    return () => {
      cancelled = true;
    };
    // contrib arrays are rebuilt each fetch; the string keys are the stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [staticKey, dynamicKey, refreshNonce]);

  return voices;
}
