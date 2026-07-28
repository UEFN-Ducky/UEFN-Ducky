import { useCallback, useEffect, useState } from "react";

export type FollowCodeSpeed = "slow" | "normal" | "fast" | "instant";

export interface FollowCodeSettings {
  /** Master switch: play the agent's file walkthrough in the editor. Content sync always happens. */
  enabled: boolean;
  /** Scales every walkthrough delay; "instant" skips highlights/previews entirely. */
  speed: FollowCodeSpeed;
  /** Agent-opened files go to a tab group beside the chat so the chat stays visible. */
  splitBesideChat: boolean;
}

const KEY_ENABLED = "uefn-follow-code-enabled";
const KEY_SPEED = "uefn-follow-code-speed";
const KEY_SPLIT = "uefn-follow-code-split";
const CHANGE_EVENT = "uefn-follow-code-settings";

const SPEED_MULTIPLIERS: Record<FollowCodeSpeed, number> = {
  slow: 1.75,
  normal: 1,
  fast: 0.45,
  instant: 0,
};

function readBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === "true") return true;
    if (raw === "false") return false;
  } catch {
    // ignore storage errors
  }
  return fallback;
}

function readSpeed(): FollowCodeSpeed {
  try {
    const raw = localStorage.getItem(KEY_SPEED);
    if (raw === "slow" || raw === "normal" || raw === "fast" || raw === "instant") return raw;
  } catch {
    // ignore storage errors
  }
  return "normal";
}

export function getFollowCodeSettings(): FollowCodeSettings {
  return {
    enabled: readBool(KEY_ENABLED, true),
    speed: readSpeed(),
    splitBesideChat: readBool(KEY_SPLIT, true),
  };
}

export function followCodeSpeedMultiplier(speed: FollowCodeSpeed): number {
  return SPEED_MULTIPLIERS[speed] ?? 1;
}

export function setFollowCodeSettings(patch: Partial<FollowCodeSettings>): void {
  try {
    if (patch.enabled !== undefined) localStorage.setItem(KEY_ENABLED, String(patch.enabled));
    if (patch.speed !== undefined) localStorage.setItem(KEY_SPEED, patch.speed);
    if (patch.splitBesideChat !== undefined) localStorage.setItem(KEY_SPLIT, String(patch.splitBesideChat));
  } catch {
    // ignore storage errors
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/** Live view of the follow-code settings (same-window edits + cross-window storage events). */
export function useFollowCodeSettings() {
  const [settings, setSettings] = useState<FollowCodeSettings>(() => getFollowCodeSettings());

  useEffect(() => {
    const refresh = () => setSettings(getFollowCodeSettings());
    const onStorage = (e: StorageEvent) => {
      if (!e.key || e.key === KEY_ENABLED || e.key === KEY_SPEED || e.key === KEY_SPLIT) refresh();
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(CHANGE_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(CHANGE_EVENT, refresh);
    };
  }, []);

  const update = useCallback((patch: Partial<FollowCodeSettings>) => {
    setFollowCodeSettings(patch);
    setSettings(getFollowCodeSettings());
  }, []);

  return { settings, update };
}
