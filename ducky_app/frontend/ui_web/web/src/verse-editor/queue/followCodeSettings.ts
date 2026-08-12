import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";

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
/** One-shot: copy legacy localStorage into panel_settings.json. */
const KEY_MIGRATED = "uefn-follow-code-migrated-v1";

const DEFAULTS: FollowCodeSettings = {
  enabled: true,
  speed: "normal",
  splitBesideChat: true,
};

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

function readLocal(): FollowCodeSettings {
  return {
    enabled: readBool(KEY_ENABLED, DEFAULTS.enabled),
    speed: readSpeed(),
    splitBesideChat: readBool(KEY_SPLIT, DEFAULTS.splitBesideChat),
  };
}

function writeLocal(settings: FollowCodeSettings): void {
  try {
    localStorage.setItem(KEY_ENABLED, String(settings.enabled));
    localStorage.setItem(KEY_SPEED, settings.speed);
    localStorage.setItem(KEY_SPLIT, String(settings.splitBesideChat));
  } catch {
    // WebView2 storage can be flaky — in-memory cache + panel_settings still win.
  }
}

/** In-memory source of truth for sync readers (editor queue). Survives localStorage failures. */
let cache: FollowCodeSettings = readLocal();
/** Bumps on every local write so a slow get_settings can't clobber a newer toggle. */
let writeGen = 0;
const listeners = new Set<() => void>();

function notify(): void {
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  for (const cb of listeners) cb();
}

export function getFollowCodeSettings(): FollowCodeSettings {
  return cache;
}

export function followCodeSpeedMultiplier(speed: FollowCodeSpeed): number {
  return SPEED_MULTIPLIERS[speed] ?? 1;
}

export function setFollowCodeSettings(patch: Partial<FollowCodeSettings>): void {
  writeGen += 1;
  cache = {
    enabled: patch.enabled ?? cache.enabled,
    speed: patch.speed ?? cache.speed,
    splitBesideChat: patch.splitBesideChat ?? cache.splitBesideChat,
  };
  writeLocal(cache);
  notify();
  const api = getApi();
  if (api?.save_agent_settings) {
    void api.save_agent_settings({
      follow_code_enabled: cache.enabled,
      follow_code_speed: cache.speed,
      follow_code_split_beside_chat: cache.splitBesideChat,
    });
  }
}

/**
 * Hydrate from panel_settings.json (durable). Migrates legacy localStorage once
 * when the disk file still has defaults but localStorage was turned off.
 */
export async function loadFollowCodeSettings(): Promise<FollowCodeSettings> {
  const api = getApi();
  if (!api?.get_settings) return cache;
  const genAtStart = writeGen;
  const s = await api.get_settings();
  if (genAtStart !== writeGen) return cache;
  const fromApi: FollowCodeSettings = {
    enabled: s.follow_code_enabled !== false,
    speed:
      s.follow_code_speed === "slow" ||
      s.follow_code_speed === "normal" ||
      s.follow_code_speed === "fast" ||
      s.follow_code_speed === "instant"
        ? s.follow_code_speed
        : "normal",
    splitBesideChat: s.follow_code_split_beside_chat !== false,
  };

  let migrated = false;
  try {
    migrated = localStorage.getItem(KEY_MIGRATED) === "1";
  } catch {
    /* ignore */
  }
  const local = readLocal();
  // User disabled via the old localStorage-only toggle — push that into panel_settings once.
  if (
    !migrated &&
    fromApi.enabled &&
    fromApi.speed === "normal" &&
    fromApi.splitBesideChat &&
    (!local.enabled || local.speed !== "normal" || !local.splitBesideChat)
  ) {
    if (genAtStart !== writeGen) return cache;
    writeGen += 1;
    cache = local;
    writeLocal(cache);
    try {
      localStorage.setItem(KEY_MIGRATED, "1");
    } catch {
      /* ignore */
    }
    if (api.save_agent_settings) {
      await api.save_agent_settings({
        follow_code_enabled: cache.enabled,
        follow_code_speed: cache.speed,
        follow_code_split_beside_chat: cache.splitBesideChat,
      });
    }
    notify();
    return cache;
  }

  if (genAtStart !== writeGen) return cache;
  cache = fromApi;
  writeLocal(cache);
  try {
    localStorage.setItem(KEY_MIGRATED, "1");
  } catch {
    /* ignore */
  }
  notify();
  return cache;
}

/** Live view of the follow-code settings (same-window edits + panel hydrate). */
export function useFollowCodeSettings() {
  const [settings, setSettings] = useState<FollowCodeSettings>(() => getFollowCodeSettings());

  useEffect(() => {
    const refresh = () => setSettings(getFollowCodeSettings());
    listeners.add(refresh);
    window.addEventListener(CHANGE_EVENT, refresh);
    void loadFollowCodeSettings().then(refresh);
    return () => {
      listeners.delete(refresh);
      window.removeEventListener(CHANGE_EVENT, refresh);
    };
  }, []);

  const update = useCallback((patch: Partial<FollowCodeSettings>) => {
    setFollowCodeSettings(patch);
    setSettings(getFollowCodeSettings());
  }, []);

  return { settings, update };
}
