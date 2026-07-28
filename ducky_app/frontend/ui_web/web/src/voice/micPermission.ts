/**
 * App-level mic consent + getUserMedia. Custom modal before OS/WebView grant.
 */

import {
  getAudioSettings,
  loadAudioSettings,
  saveAudioSettings,
  type MicPermission,
} from "./audioSettings";

export type MicDeviceInfo = {
  deviceId: string;
  label: string;
};

export type MicDeviceList = {
  /** Non-default / non-communications inputs for the picker. */
  devices: MicDeviceInfo[];
  /** Friendly name of the Windows/OS current default mic. */
  defaultLabel: string;
};

export type OutputDeviceList = {
  devices: MicDeviceInfo[];
  defaultLabel: string;
};

function cleanDefaultLabel(raw: string, fallback: string): string {
  const s = (raw || "").trim();
  if (!s) return fallback;
  return s.replace(/^Default\s*[-–—:]\s*/i, "").trim() || s;
}

type PromptResolver = (allowed: boolean) => void;

let promptOpen = false;
let promptResolve: PromptResolver | null = null;
const promptListeners = new Set<(open: boolean) => void>();

function notifyPrompt(open: boolean) {
  promptOpen = open;
  for (const fn of promptListeners) fn(open);
}

export function subscribeMicPermissionPrompt(fn: (open: boolean) => void): () => void {
  promptListeners.add(fn);
  fn(promptOpen);
  return () => promptListeners.delete(fn);
}

export function isMicPermissionPromptOpen(): boolean {
  return promptOpen;
}

/** Resolve the in-app Allow/Block prompt (called from MicPermissionModal). */
export function resolveMicPermissionPrompt(allowed: boolean): void {
  const resolve = promptResolve;
  promptResolve = null;
  notifyPrompt(false);
  resolve?.(allowed);
}

function askViaModal(): Promise<boolean> {
  if (promptResolve) {
    // Already open — reuse the same prompt.
    return new Promise((resolve) => {
      const prev = promptResolve!;
      promptResolve = (allowed) => {
        prev(allowed);
        resolve(allowed);
      };
    });
  }
  return new Promise((resolve) => {
    promptResolve = resolve;
    notifyPrompt(true);
  });
}

function baseAudioConstraints(deviceId: string): MediaTrackConstraints {
  const audio: MediaTrackConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  const id = (deviceId || "").trim();
  if (id) {
    // exact pins the saved device; openMicStream falls back if it's gone.
    audio.deviceId = { exact: id };
  }
  return audio;
}

/** Constraints for the preferred mic (saved id, or OS default when empty). */
export function micConstraints(deviceId?: string): MediaStreamConstraints {
  const id = (deviceId ?? getAudioSettings().micDeviceId ?? "").trim();
  return { audio: baseAudioConstraints(id) };
}

/** Raw getUserMedia — caller must have app consent already. Honors saved micDeviceId. */
export async function openMicStream(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone not available in this WebView");
  }
  await loadAudioSettings();
  const preferred = (getAudioSettings().micDeviceId || "").trim();
  try {
    return await navigator.mediaDevices.getUserMedia(micConstraints(preferred));
  } catch {
    if (preferred) {
      try {
        return await navigator.mediaDevices.getUserMedia(micConstraints(""));
      } catch {
        /* fall through */
      }
    }
    // Some WebViews reject constrained audio — plain audio still works.
    return navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  }
}

/**
 * Gate mic access: honor ask/allow/block, show branded modal when ask.
 * On Allow, persists allow and returns a live MediaStream.
 */
export async function requestMicAccess(): Promise<MediaStream> {
  await loadAudioSettings();
  let perm: MicPermission = getAudioSettings().micPermission;

  if (perm === "block") {
    throw new Error("Microphone blocked. Enable it in Settings → Audio.");
  }

  if (perm === "ask") {
    const allowed = await askViaModal();
    if (!allowed) {
      await saveAudioSettings({ micPermission: "block" });
      throw new Error("Microphone blocked. Enable it in Settings → Audio.");
    }
    await saveAudioSettings({ micPermission: "allow" });
    perm = "allow";
  }

  try {
    return await openMicStream();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (/Permission|NotAllowed|denied/i.test(msg)) {
      throw new Error(
        "Microphone denied by the system. Check Windows mic privacy for UEFN Ducky, then try again.",
      );
    }
    throw err instanceof Error ? err : new Error(msg);
  }
}

/** Pure gate for tests — no getUserMedia. */
export function micAccessAllowed(permission: MicPermission): boolean {
  return permission === "allow";
}

function listKind(
  kind: MediaDeviceKind,
  fallbackDefault: string,
): Promise<{ devices: MicDeviceInfo[]; defaultLabel: string }> {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return Promise.resolve({ devices: [], defaultLabel: fallbackDefault });
  }
  return navigator.mediaDevices.enumerateDevices().then((devices) => {
    const ofKind = devices.filter((d) => d.kind === kind);
    const defaultDev = ofKind.find((d) => d.deviceId === "default");
    const defaultLabel = cleanDefaultLabel(
      defaultDev?.label || ofKind.find((d) => d.label)?.label || "",
      fallbackDefault,
    );
    const listed = ofKind
      .filter((d) => d.deviceId !== "default" && d.deviceId !== "communications")
      .map((d, i) => ({
        deviceId: d.deviceId,
        label: d.label || `${kind === "audioinput" ? "Microphone" : "Speaker"} ${i + 1}`,
      }));
    return { devices: listed, defaultLabel };
  });
}

export async function listMicDevices(): Promise<MicDeviceList> {
  return listKind("audioinput", "Current Windows microphone");
}

export async function listOutputDevices(): Promise<OutputDeviceList> {
  return listKind("audiooutput", "Current Windows speaker");
}

export async function setMicPermission(permission: MicPermission): Promise<void> {
  await saveAudioSettings({ micPermission: permission });
}
