import { useCallback, useEffect, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import { getDirectTransport } from "../remote/directTransport";
import { useDictation } from "../voice/useDictation";
import {
  HOLD_MS,
  motionToCursor,
  padDown,
  padHoldStartsDrag,
  padMove,
  padUp,
  scrollSteps,
  takeWhole,
  type PadTouch,
} from "./remoteTrackpadMath";

const SETTINGS_KEY = "ducky.remoteTrackpad";
const CLICK_MS = 60;
/** A tap jolts the phone: the air mouse ignores motion this long around every touch. */
const TOUCH_MOTION_MUTE_MS = 220;
const MOTION_WAIT_MS = 2000;
const KEYS: { key: string; label: string }[] = [
  { key: "Enter", label: "Enter" },
  { key: "Backspace", label: "Delete" },
  { key: "Tab", label: "Tab" },
  { key: "Escape", label: "Esc" },
];

type Settings = { speed: number; motionSpeed: number; invertX: boolean; invertY: boolean };
const DEFAULT_SETTINGS: Settings = { speed: 2, motionSpeed: 1, invertX: false, invertY: false };

function loadSettings(): Settings {
  try {
    return { ...DEFAULT_SETTINGS, ...(JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") as Partial<Settings>) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

type Payload = Record<string, unknown>;

/** The phone as the PC's mouse: finger on the pad, or the whole phone as an air mouse. No video. */
export function RemoteTrackpad() {
  const [settings, setSettings] = useState(loadSettings);
  const [paused, setPaused] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [motion, setMotion] = useState(false);
  const [motionError, setMotionError] = useState("");
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [linked, setLinked] = useState(true);

  const pausedRef = useRef(false);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const pad = useRef<{ id: number; touch: PadTouch } | null>(null);
  const holdTimer = useRef(0);
  const scroll = useRef<{ id: number; y: number; acc: number } | null>(null);
  const pending = useRef({ x: 0, y: 0 });
  const frame = useRef(0);
  const muteMotionUntil = useRef(0);
  const talking = useRef(false);
  const micStarted = useRef<Promise<void>>(Promise.resolve());
  const [interim, setInterim] = useState("");

  const send = useCallback((payload: Payload) => {
    if (!pausedRef.current) getDirectTransport()?.sendInput({ view: "desktop", ...payload });
  }, []);

  // Words land wherever the PC's text cursor is. The space keeps one phrase off the next.
  const dictation = useDictation({
    onInterim: setInterim,
    onFinal: (words) => send({ type: "text", text: `${words} ` }),
  });

  const queueMove = useCallback(
    (dx: number, dy: number) => {
      pending.current.x += dx;
      pending.current.y += dy;
      if (frame.current) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = 0;
        const whole = takeWhole(pending.current.x, pending.current.y);
        pending.current = { x: whole.restX, y: whole.restY };
        if (whole.dx || whole.dy) send({ type: "move", dx: whole.dx, dy: whole.dy });
      });
    },
    [send],
  );

  const click = useCallback(
    (button: number) => {
      send({ type: "down", button });
      window.setTimeout(() => send({ type: "up", button }), CLICK_MS);
    },
    [send],
  );

  /** Lets go of the PC's mouse button if a drag is held; a stuck button is the worst way to end. */
  const releasePad = useCallback(() => {
    window.clearTimeout(holdTimer.current);
    if (pad.current?.touch.dragging) send({ type: "up", button: 0 });
    pad.current = null;
    setDragging(false);
  }, [send]);

  useEffect(() => {
    const transport = getDirectTransport();
    const stop = transport?.onStatus((status) => setLinked(status.state === "live"));
    window.addEventListener("pagehide", releasePad);
    return () => {
      stop?.();
      window.removeEventListener("pagehide", releasePad);
      releasePad();
      if (frame.current) cancelAnimationFrame(frame.current);
    };
  }, [releasePad]);

  // The screen must stay on for the air mouse, when no finger is touching it.
  useEffect(() => {
    let live = true;
    let lock: WakeLockSentinel | undefined;
    navigator.wakeLock
      ?.request("screen")
      .then((held) => {
        if (live) lock = held;
        else void held.release();
      })
      .catch(() => {});
    return () => {
      live = false;
      void lock?.release();
    };
  }, []);

  useEffect(() => {
    if (!motion || paused) return;
    let last = 0;
    let seen = false;
    const onMotion = (event: DeviceMotionEvent) => {
      const rate = event.rotationRate;
      if (!rate || rate.alpha === null) return;
      if (!seen) setMotionError("");
      seen = true;
      const now = performance.now();
      const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
      last = now;
      const fingerOnPad = !!pad.current && !pad.current.touch.dragging;
      // Talking freezes the air mouse, so the cursor stays on the box being typed into.
      if (fingerOnPad || scroll.current || talking.current || now < muteMotionUntil.current) return;
      const g = event.accelerationIncludingGravity;
      const tune = settingsRef.current;
      const move = motionToCursor(
        { alpha: rate.alpha ?? 0, beta: rate.beta ?? 0, gamma: rate.gamma ?? 0 },
        g && g.x !== null ? { x: g.x ?? 0, y: g.y ?? 0, z: g.z ?? 0 } : null,
        dt,
        { sensitivity: tune.motionSpeed, invertX: tune.invertX, invertY: tune.invertY },
      );
      queueMove(move.dx, move.dy);
    };
    window.addEventListener("devicemotion", onMotion);
    const noData = window.setTimeout(() => {
      if (!seen) setMotionError("This phone isn't sending motion data here.");
    }, MOTION_WAIT_MS);
    return () => {
      window.removeEventListener("devicemotion", onMotion);
      window.clearTimeout(noData);
    };
  }, [motion, paused, queueMove]);

  const toggleMotion = async () => {
    if (motion) {
      setMotion(false);
      setMotionError("");
      return;
    }
    // iPhone only: must be asked from inside the tap.
    const sensor = window.DeviceMotionEvent as unknown as { requestPermission?: () => Promise<string> } | undefined;
    if (sensor?.requestPermission) {
      const answer = await sensor.requestPermission().catch(() => "denied");
      if (answer !== "granted") {
        setMotionError("Motion access was refused. Allow it for this site to use the air mouse.");
        return;
      }
    }
    setMotionError("");
    setMotion(true);
  };

  const togglePause = () => {
    if (!paused) {
      releasePad();
      talking.current = false;
      dictation.abort();
    }
    pausedRef.current = !paused;
    setPaused(!paused);
  };

  const onTalkDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (pausedRef.current || talking.current || dictation.status === "transcribing") return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    talking.current = true;
    micStarted.current = dictation.start();
  };

  /** Waits for the mic to finish starting: letting go during startup must still end it. */
  const onTalkUp = () => {
    if (!talking.current) return;
    talking.current = false;
    void micStarted.current.then(() => {
      if (!talking.current) void dictation.stop();
    });
  };

  const onPadDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (pausedRef.current || pad.current) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pad.current = { id: event.pointerId, touch: padDown(event.clientX, event.clientY) };
    holdTimer.current = window.setTimeout(() => {
      const touch = pad.current?.touch;
      if (!touch || !padHoldStartsDrag(touch)) return;
      touch.dragging = true;
      setDragging(true);
      send({ type: "down", button: 0 });
      navigator.vibrate?.(20);
    }, HOLD_MS);
  };

  const onPadMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (pad.current?.id !== event.pointerId) return;
    const { dx, dy } = padMove(pad.current.touch, event.clientX, event.clientY, settingsRef.current.speed);
    if (dx || dy) queueMove(dx, dy);
  };

  const onPadUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (pad.current?.id !== event.pointerId) return;
    const isClick = padUp(pad.current.touch) === "click";
    releasePad();
    if (isClick) click(0);
  };

  const onScrollDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    scroll.current = { id: event.pointerId, y: event.clientY, acc: 0 };
  };

  const onScrollMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const strip = scroll.current;
    if (strip?.id !== event.pointerId) return;
    const { steps, acc } = scrollSteps(strip.acc, event.clientY - strip.y);
    strip.y = event.clientY;
    strip.acc = acc;
    if (steps) send({ type: "wheel", delta: steps });
  };

  const onScrollUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (scroll.current?.id === event.pointerId) scroll.current = null;
  };

  const patchSettings = (patch: Partial<Settings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
    } catch {
      /* private mode: the settings last until the page closes */
    }
  };

  const tapKey = (key: string) => {
    send({ type: "keydown", key });
    window.setTimeout(() => send({ type: "keyup", key }), CLICK_MS);
  };

  const submitText = (event: FormEvent) => {
    event.preventDefault();
    if (text) send({ type: "text", text });
    setText("");
  };

  const muteMotion = () => {
    muteMotionUntil.current = performance.now() + TOUCH_MOTION_MUTE_MS;
  };

  const hint = paused ? "Paused" : dragging ? "Dragging. Lift to drop" : motion ? "Move the phone to point" : "Tap to click · hold to drag";

  return (
    <div className="remote-window-overlay">
      <div className="remote-trackpad" onPointerDownCapture={muteMotion} onPointerUpCapture={muteMotion}>
        <div className="remote-trackpad-status" role="status">
          {linked ? "Connected to this PC's mouse" : "Reconnecting to the PC…"}
        </div>
        <div className="remote-trackpad-surfaces">
          <div
            className={`remote-trackpad-pad${dragging ? " is-dragging" : ""}`}
            data-testid="trackpad-pad"
            onPointerDown={onPadDown}
            onPointerMove={onPadMove}
            onPointerUp={onPadUp}
            onPointerCancel={releasePad}
          >
            <span>{interim || hint}</span>
          </div>
          <div
            className="remote-trackpad-scroll"
            data-testid="trackpad-scroll"
            onPointerDown={onScrollDown}
            onPointerMove={onScrollMove}
            onPointerUp={onScrollUp}
            onPointerCancel={onScrollUp}
          >
            <span>Scroll</span>
          </div>
        </div>
        {motionError ? <div className="remote-trackpad-error">{motionError}</div> : null}
        {dictation.notice ? <div className="remote-trackpad-error">{dictation.notice.message}</div> : null}
        <div className="remote-trackpad-buttons">
          <button type="button" onClick={() => click(2)}>
            Right click
          </button>
          <button type="button" onClick={() => send({ type: "move", x: 0.5, y: 0.5, view: "monitor:0" })}>
            Center
          </button>
          <button type="button" aria-pressed={motion} className={motion ? "is-on" : ""} onClick={() => void toggleMotion()}>
            Motion
          </button>
          <button type="button" aria-pressed={paused} className={paused ? "is-on" : ""} onClick={togglePause}>
            {paused ? "Resume" : "Pause"}
          </button>
          <button type="button" aria-pressed={typing} className={typing ? "is-on" : ""} onClick={() => setTyping(!typing)}>
            Keyboard
          </button>
          <button
            type="button"
            className={`remote-trackpad-talk${dictation.isRecording ? " is-on" : ""}`}
            aria-label="Hold to talk"
            onPointerDown={onTalkDown}
            onPointerUp={onTalkUp}
            onPointerCancel={onTalkUp}
            onContextMenu={(event) => event.preventDefault()}
          >
            {dictation.isRecording ? "Listening…" : dictation.status === "transcribing" ? "Typing…" : "Hold to talk"}
          </button>
        </div>
        {typing ? (
          <div className="remote-trackpad-typing">
            <form onSubmit={submitText}>
              <input
                value={text}
                aria-label="Text to type on the PC"
                placeholder="Type on the PC"
                autoComplete="off"
                enterKeyHint="send"
                onChange={(event) => setText(event.target.value)}
              />
              <button type="submit">Send</button>
            </form>
            <div className="view-menu-chips">
              {KEYS.map((item) => (
                <button key={item.key} type="button" className="view-menu-chip" onClick={() => tapKey(item.key)}>
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <details className="remote-trackpad-settings">
          <summary>Settings</summary>
          <label className="remote-control-slider">
            <span>
              Trackpad speed <b>{settings.speed.toFixed(1)}</b>
            </span>
            <input type="range" min={0.5} max={5} step={0.1} value={settings.speed} onChange={(e) => patchSettings({ speed: Number(e.target.value) })} />
          </label>
          <label className="remote-control-slider">
            <span>
              Motion speed <b>{settings.motionSpeed.toFixed(1)}</b>
            </span>
            <input type="range" min={0.2} max={4} step={0.1} value={settings.motionSpeed} onChange={(e) => patchSettings({ motionSpeed: Number(e.target.value) })} />
          </label>
          <label className="remote-trackpad-check">
            <input type="checkbox" checked={settings.invertX} onChange={(e) => patchSettings({ invertX: e.target.checked })} /> Invert motion left/right
          </label>
          <label className="remote-trackpad-check">
            <input type="checkbox" checked={settings.invertY} onChange={(e) => patchSettings({ invertY: e.target.checked })} /> Invert motion up/down
          </label>
        </details>
      </div>
    </div>
  );
}
