/**
 * Listens for ducky:hook events and plays the mapped sound.
 * Mount once under AppearanceProvider.
 */

import { useEffect, useMemo } from "react";
import { useAppearance } from "../theme/AppearanceContext";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installPanelPushBus, isMainWindow, subscribePanelPush } from "../hooks/usePanelPushBus";
import { emitAppHook, subscribeAppHooks } from "./appHooks";
import { playHookSound, playSoundRef } from "./soundFx";

/** Older pushes are a reloaded window catching up, not a hook that just fired. */
const PLUGIN_HOOK_FRESH_S = 10;

export function SoundFxBridge() {
  const { sounds } = useAppearance();
  const contrib = usePluginContributions();

  const pluginFileByKey = useMemo(() => {
    const map: Record<string, string> = {};
    for (const s of contrib.sounds || []) {
      if (s.plugin_id && s.id && s.file) {
        map[`${s.plugin_id}:${s.id}`] = s.file;
      }
    }
    return map;
  }, [contrib.sounds]);

  useEffect(() => {
    return subscribeAppHooks((detail) => {
      playHookSound(sounds, detail.id, pluginFileByKey);
    });
  }, [sounds, pluginFileByKey]);

  // A plugin's api.emit_hook reaches every window; only the main one fires it, so it plays once.
  useEffect(() => {
    if (!isMainWindow()) return;
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "plugin_hook" || !event.id) return;
      if (event.at && Date.now() / 1000 - event.at > PLUGIN_HOOK_FRESH_S) return;
      emitAppHook(event.id, { pluginId: event.plugin_id || "", payload: event.payload || {} });
    });
  }, []);

  return null;
}

/** Preview a sound from settings (ignores SFX enable + master mute). */
export function previewSound(ref: string, volume: number, pluginFileByKey?: Record<string, string>): void {
  playSoundRef(ref, volume, pluginFileByKey, { ignoreMasterMute: true });
}
