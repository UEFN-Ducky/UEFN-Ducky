/**
 * Appearance → Sounds accordion body. Lives in sfx/ so AppearanceTab stays thin.
 */

import { useMemo } from "react";
import { ChoiceDropdown, type ChoiceOption } from "../components/ChoiceDropdown";
import { useAppearance } from "../theme/AppearanceContext";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { getApi } from "../hooks/usePanelApi";
import { APP_HOOKS } from "./appHooks";
import { BUILTIN_SOUNDS } from "./builtinSounds";
import { previewSound } from "./SoundFxBridge";
import type { SoundRef } from "./soundFx";

const CUSTOM_FILE_VALUE = "__custom_file__";

export function SoundsSection() {
  const { sounds, setSounds } = useAppearance();
  const contrib = usePluginContributions();

  const pluginHooks = useMemo(
    () =>
      (contrib.hooks || []).map((h) => ({
        id: `plugin:${h.plugin_id}:${h.id}`,
        label: h.label || h.id,
      })),
    [contrib.hooks],
  );

  const allHooks = useMemo(() => [...APP_HOOKS, ...pluginHooks], [pluginHooks]);

  const pluginSounds = useMemo(() => contrib.sounds || [], [contrib.sounds]);

  const pluginFileByKey = useMemo(() => {
    const map: Record<string, string> = {};
    for (const s of pluginSounds) {
      if (s.plugin_id && s.id && s.file) {
        map[`${s.plugin_id}:${s.id}`] = s.file;
      }
    }
    return map;
  }, [pluginSounds]);

  const setEnabled = (enabled: boolean) => {
    void setSounds({ ...sounds, enabled });
  };

  const setVolume = (volume: number) => {
    void setSounds({ ...sounds, volume });
  };

  const setHookSound = (hookId: string, ref: SoundRef) => {
    void setSounds({
      ...sounds,
      mapping: { ...sounds.mapping, [hookId]: ref },
    });
  };

  const pickCustomFile = async (hookId: string) => {
    const api = getApi();
    if (!api?.import_sound_file) return;
    try {
      const result = await api.import_sound_file();
      if (!result?.ok || !result.filename) return;
      setHookSound(hookId, `file:${result.filename}`);
    } catch {
      /* ignore */
    }
  };

  const onSelectChange = (hookId: string, value: string) => {
    if (value === CUSTOM_FILE_VALUE) {
      void pickCustomFile(hookId);
      return;
    }
    setHookSound(hookId, value);
  };

  return (
    <div className="appearance-sounds">
      <div className="appearance-sounds-mute-bar">
        <button
          type="button"
          className={`appearance-sounds-mute-btn${sounds.enabled ? "" : " is-muted"}`}
          onClick={() => setEnabled(!sounds.enabled)}
          title={sounds.enabled ? "Mute all sound effects" : "Unmute sound effects"}
        >
          {sounds.enabled ? "Mute all" : "Unmute"}
        </button>
        <span className="appearance-sounds-mute-hint">
          {sounds.enabled ? "Sound effects on" : "All sound effects muted"}
        </span>
      </div>
      <label className="appearance-sounds-row">
        <span>Enable sound effects</span>
        <input
          type="checkbox"
          checked={sounds.enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
      </label>
      <label className="appearance-sounds-row">
        <span>Volume</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={sounds.volume}
          disabled={!sounds.enabled}
          onChange={(e) => setVolume(Number(e.target.value))}
        />
        <span className="appearance-sounds-vol">{Math.round(sounds.volume * 100)}%</span>
      </label>

      <div className="appearance-sounds-hooks">
        {allHooks.map((hook) => {
          const current = sounds.mapping[hook.id] ?? "";
          const isFile = current.startsWith("file:");
          const options: ChoiceOption[] = [
            { value: "", label: "None" },
            ...BUILTIN_SOUNDS.map((s) => ({
              value: `builtin:${s.id}`,
              label: s.label,
              group: "Built-in",
            })),
            ...pluginSounds.map((s) => ({
              value: `plugin:${s.plugin_id}:${s.id}`,
              label: s.label || s.id,
              group: "Plugins",
            })),
            ...(isFile
              ? [{ value: current, label: `Custom: ${current.slice("file:".length)}`, group: "Custom" }]
              : []),
            { value: CUSTOM_FILE_VALUE, label: "Custom file…", group: "Custom" },
          ];
          return (
            <div key={hook.id} className="appearance-sounds-hook">
              <span className="appearance-sounds-hook-label">{hook.label}</span>
              <ChoiceDropdown
                size="compact"
                mode="radio"
                value={current}
                disabled={!sounds.enabled}
                aria-label={`${hook.label} sound`}
                options={options}
                onChange={(next) => onSelectChange(hook.id, next)}
              />
              <button
                type="button"
                className="btn-ghost"
                disabled={!sounds.enabled || !current}
                onClick={() => previewSound(current, sounds.volume, pluginFileByKey)}
                title="Preview"
              >
                ▶
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
