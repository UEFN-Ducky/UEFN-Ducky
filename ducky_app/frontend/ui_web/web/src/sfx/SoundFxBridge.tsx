/**
 * Listens for ducky:hook events and plays the mapped sound.
 * Mount once under AppearanceProvider.
 */

import { useEffect, useMemo } from "react";
import { useAppearance } from "../theme/AppearanceContext";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { subscribeAppHooks } from "./appHooks";
import { playHookSound, playSoundRef } from "./soundFx";

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

  return null;
}

/** Preview a sound from settings (ignores SFX enable + master mute). */
export function previewSound(ref: string, volume: number, pluginFileByKey?: Record<string, string>): void {
  playSoundRef(ref, volume, pluginFileByKey, { ignoreMasterMute: true });
}
