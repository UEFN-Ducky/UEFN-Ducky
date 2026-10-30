/**
 * Keeps the phone awake while you talk to Ducky: a live chat or dictation holds it.
 *
 * - Phone panel (an iframe on the site's Remote page): Capacitor only runs plugin
 *   calls from the main frame, so this asks the page (`ud-keep-awake`), which starts
 *   the app's `LiveVoice` service and keeps the screen on.
 * - Top frame (the app's own page, a phone browser): the `LiveVoice` plugin when the
 *   Android shell has it, plus the browser's screen wake lock.
 * - Desktop: neither exists, so every call is a no-op.
 */

type LiveVoicePlugin = {
  start: () => Promise<unknown>;
  stop: () => Promise<unknown>;
};

type WakeLockSentinelLike = { release: () => Promise<void>; addEventListener: (t: "release", fn: () => void) => void };

/** The notification's Stop button (sent by the Android shell). */
export const NATIVE_LIVE_VOICE_STOP = "ud:live-voice-stop";
/** Panel → site page: keep the phone awake (on) or let it sleep. */
export const UD_KEEP_AWAKE = "ud-keep-awake";
/** Site page → panel: the notification's Stop was pressed. */
export const UD_LIVE_VOICE_STOP = "ud-live-voice-stop";

export type AwakeHolder = "live" | "dictation";

const holders = new Set<AwakeHolder>();
let running = false;
let stoppedByNotification = false;
let screenLock: WakeLockSentinelLike | null = null;
let listening = false;

function plugin(): LiveVoicePlugin | null {
  const cap = (globalThis as { Capacitor?: { Plugins?: Record<string, unknown> } }).Capacitor;
  const p = cap?.Plugins?.LiveVoice as Partial<LiveVoicePlugin> | undefined;
  if (!p || typeof p.start !== "function" || typeof p.stop !== "function") return null;
  return p as LiveVoicePlugin;
}

function framed(): boolean {
  try {
    return typeof window !== "undefined" && window.parent !== window;
  } catch {
    return true;
  }
}

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** The browser's screen wake lock; it drops itself when the page is hidden, so it is asked again on return. */
async function holdScreen(on: boolean): Promise<void> {
  if (!on) {
    const lock = screenLock;
    screenLock = null;
    await lock?.release().catch(() => undefined);
    return;
  }
  const wl = (globalThis.navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<WakeLockSentinelLike> } })
    ?.wakeLock;
  if (screenLock || !wl || hidden()) return;
  try {
    const lock = await wl.request("screen");
    if (!running) {
      void lock.release().catch(() => undefined);
      return;
    }
    screenLock = lock;
    lock.addEventListener("release", () => {
      if (screenLock === lock) screenLock = null;
    });
  } catch {
    /* refused (battery saver, no permission): the phone may still sleep */
  }
}

function listen(): void {
  if (listening || typeof document === "undefined") return;
  listening = true;
  document.addEventListener("visibilitychange", () => {
    if (!hidden() && running && !framed()) void holdScreen(true);
  });
  // The page relays the notification's Stop into the panel.
  window.addEventListener("message", (ev: MessageEvent) => {
    if (ev.source !== window.parent || ev.origin !== window.location.origin) return;
    if ((ev.data as { type?: string } | null)?.type === UD_LIVE_VOICE_STOP) {
      document.dispatchEvent(new CustomEvent(NATIVE_LIVE_VOICE_STOP));
    }
  });
}

function apply(want: boolean): void {
  if (want === running) return;
  running = want;
  if (framed()) {
    window.parent.postMessage({ type: UD_KEEP_AWAKE, on: want }, window.location.origin);
    return;
  }
  void holdScreen(want);
  const p = plugin();
  if (!p) return;
  const call = want ? p.start() : p.stop();
  // Refused (no mic permission, app in the background): live voice still works on screen.
  void Promise.resolve(call).catch(() => {
    if (want) running = false;
  });
}

/** One reason to stay awake turned on or off; awake while any is on. */
export function holdAwake(holder: AwakeHolder, on: boolean): void {
  listen();
  if (on) holders.add(holder);
  else holders.delete(holder);
  const want = holders.size > 0;
  if (stoppedByNotification) {
    // Chats end one by one after the notification's Stop: don't restart in between.
    if (!want) stoppedByNotification = false;
    return;
  }
  apply(want);
}

/** Live chats: start on the first, stop after the last. */
export function syncNativeKeepAlive(liveCount: number): void {
  holdAwake("live", liveCount > 0);
}

/**
 * Ends live voice when the notification's Stop is pressed. `endAll` returns how
 * many live chats it is ending. Returns an unsubscribe.
 */
export function onNativeLiveVoiceStop(endAll: () => number): () => void {
  if (typeof document === "undefined") return () => {};
  listen();
  const fn = () => {
    // The service already stopped itself: no stop() call back.
    running = false;
    void holdScreen(false);
    stoppedByNotification = endAll() > 0;
  };
  document.addEventListener(NATIVE_LIVE_VOICE_STOP, fn);
  return () => document.removeEventListener(NATIVE_LIVE_VOICE_STOP, fn);
}
