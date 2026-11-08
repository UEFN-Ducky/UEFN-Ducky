import { useCallback, useEffect, useRef } from "react";
import { StickBtn, UefnStickPad } from "./RemoteWindowView";
import { keyDiff, stickLookDelta, stickMoveKeys } from "./remoteWindowMath";

const DEADZONE = 0.2;

type Payload = Record<string, unknown>;
/** A key name, or a mouse button number. */
type Control = { label: string; key?: string; button?: number; className?: string };

// ponytail: Fortnite's default keyboard and mouse bindings; a rebound game needs a binding editor.
const TOP: Control[] = [
  { label: "1", key: "1" },
  { label: "2", key: "2" },
  { label: "3", key: "3" },
  { label: "4", key: "4" },
  { label: "5", key: "5" },
  { label: "6", key: "6" },
  { label: "Map", key: "m" },
  { label: "Bag", key: "i" },
  { label: "Tab", key: "Tab" },
  { label: "Esc", key: "Escape" },
];
const LEFT: Control[] = [
  { label: "Sprint", key: "Shift" },
  { label: "Crouch", key: "Control" },
];
const CENTER: Control[] = [
  { label: "Use", key: "e" },
  { label: "Reload", key: "r" },
  { label: "Emote", key: "b" },
];
const RIGHT: Control[] = [
  { label: "Aim", button: 2 },
  { label: "Fire", button: 0, className: "remote-controller-fire" },
  { label: "Jump", key: " ", className: "remote-controller-jump" },
];

/** Two sticks and game buttons for the PC's game. No video: you watch the PC. */
export function RemoteController({ send, lookSpeed }: { send: (payload: Payload) => void; lookSpeed: number }) {
  const moveKeys = useRef<string[]>([]);
  const held = useRef(new Map<string, Payload>());
  const look = useRef({ nx: 0, ny: 0, on: false });
  const frame = useRef(0);
  const speed = useRef(lookSpeed);
  speed.current = lookSpeed;

  const stopLook = useCallback(() => {
    look.current = { nx: 0, ny: 0, on: false };
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
  }, []);

  /** Nothing may stay pressed on the PC once the controller goes away. */
  const releaseAll = useCallback(() => {
    for (const key of moveKeys.current) send({ type: "keyup", key });
    moveKeys.current = [];
    for (const release of held.current.values()) send(release);
    held.current.clear();
    stopLook();
  }, [send, stopLook]);

  useEffect(() => {
    window.addEventListener("pagehide", releaseAll);
    return () => {
      window.removeEventListener("pagehide", releaseAll);
      releaseAll();
    };
  }, [releaseAll]);

  const onMove = (nx: number, ny: number, active: boolean) => {
    const next = active ? stickMoveKeys(nx, ny, DEADZONE) : [];
    const diff = keyDiff(moveKeys.current, next);
    moveKeys.current = next;
    for (const key of diff.down) send({ type: "keydown", key });
    for (const key of diff.up) send({ type: "keyup", key });
  };

  // No mouse button here: holding the right one, as the editor needs, would aim in a game.
  const onLook = (nx: number, ny: number, active: boolean) => {
    if (!active) {
      stopLook();
      return;
    }
    look.current = { nx, ny, on: true };
    if (frame.current) return;
    const tick = () => {
      if (!look.current.on) {
        frame.current = 0;
        return;
      }
      const { dx, dy } = stickLookDelta(look.current.nx, look.current.ny, speed.current, DEADZONE);
      if (dx || dy) send({ type: "move", dx, dy });
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  };

  const press = (control: Control) => {
    if (held.current.has(control.label)) return;
    if (control.key !== undefined) {
      send({ type: "keydown", key: control.key });
      held.current.set(control.label, { type: "keyup", key: control.key });
    } else {
      send({ type: "down", button: control.button });
      held.current.set(control.label, { type: "up", button: control.button });
    }
  };

  const release = (control: Control) => {
    const payload = held.current.get(control.label);
    if (!payload) return;
    held.current.delete(control.label);
    send(payload);
  };

  const buttons = (controls: Control[]) =>
    controls.map((control) => (
      <StickBtn
        key={control.label}
        label={control.label}
        className={control.className}
        onDown={() => press(control)}
        onUp={() => release(control)}
      />
    ));

  return (
    <div className="remote-controller">
      <div className="remote-controller-top">{buttons(TOP)}</div>
      <div className="remote-controller-side">
        <div className="remote-controller-row">{buttons(LEFT)}</div>
        <UefnStickPad className="remote-stick remote-controller-stick" onChange={onMove} testId="controller-move" />
        <span className="remote-controller-label">Move</span>
      </div>
      <div className="remote-controller-center">{buttons(CENTER)}</div>
      <div className="remote-controller-side">
        <div className="remote-controller-row">{buttons(RIGHT)}</div>
        <UefnStickPad className="remote-stick remote-controller-stick" onChange={onLook} testId="controller-look" />
        <span className="remote-controller-label">Look</span>
      </div>
    </div>
  );
}
