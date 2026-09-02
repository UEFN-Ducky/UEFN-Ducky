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

/** pywebview / JSON / localStorage can hand back a real bool or "false"/"0". */
export function readApiBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const raw = value.trim().toLowerCase();
    if (raw === "true" || raw === "1" || raw === "yes" || raw === "on") return true;
    if (raw === "false" || raw === "0" || raw === "no" || raw === "off") return false;
  }
  return fallback;
}

function readBool(key: string, fallback: boolean): boolean {
  try {
    return readApiBool(localStorage.getItem(key), fallback);
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

/** Agent opens land beside chat only when Follow Code is on AND the tab-group switch is on. */
export function shouldSplitFollowTabs(): boolean {
  return cache.enabled && cache.splitBesideChat;
}

export function followCodeSpeedMultiplier(speed: FollowCodeSpeed): number {
  return SPEED_MULTIPLIERS[speed] ?? 1;
}

export function setFollowCodeSettings(patch: Partial<FollowCodeSettings>): void {
  writeGen += 1;
  const enabled = patch.enabled ?? cache.enabled;
  // Follow off ⇒ tab-group open off. Leaving split checked+disabled was why
  // files still opened beside chat after the user unchecked Follow Code.
  const splitBesideChat =
    enabled === false
      ? (patch.splitBesideChat ?? false)
      : (patch.splitBesideChat ?? cache.splitBesideChat);
  cache = {
    enabled,
    speed: patch.speed ?? cache.speed,
    splitBesideChat,
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
    enabled: readApiBool(s.follow_code_enabled, DEFAULTS.enabled),
    speed:
      s.follow_code_speed === "slow" ||
      s.follow_code_speed === "normal" ||
      s.follow_code_speed === "fast" ||
      s.follow_code_speed === "instant"
        ? s.follow_code_speed
        : "normal",
    splitBesideChat: readApiBool(s.follow_code_split_beside_chat, DEFAULTS.splitBesideChat),
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
    // App.tsx hydrates once. Reloading here on every Settings remount raced the
    // fire-and-forget save and snapped the checkbox back to on.
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
