import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceTriggerFace } from "./ChoiceDropdown";
import { DropdownPanel } from "./DropdownPanel";
import { DuckyParade } from "./DuckyParade";
import { Icons } from "../icons/Icons";
import {
  addViewPan,
  buttonOnly,
  chordTap,
  clampView,
  contentRect,
  fillsHeight,
  ignoreMouseAfterTouch,
  isDoubleTap,
  keyDiff,
  linkFrozen,
  mapViewPoint,
  newLinkWatch,
  panZoomAround,
  pickUeFnFollow,
  pinchScale,
  rankVideoCodec,
  stickLookDelta,
  stickMoveKeys,
  twoPointCenter,
  twoPointDist,
  withinSlop,
  type ViewPanZoom,
} from "./remoteWindowMath";
import { getDirectTransport } from "../remote/directTransport";
import { setRemotePicture } from "../remote/remotePicture";
import { setVisibleInterval } from "../utils/visibleInterval";

export { contentRect } from "./remoteWindowMath";

export type WindowViewRow = { id: string; title: string; kind?: string };

type SendFn = (payload: Record<string, unknown>) => void;
type ControlMode = "editor" | "play" | "editing";
type ControlState = {
  mode: ControlMode;
  overlay: boolean;
  look: number;
  deadzone: number;
  leftSize: number;
  rightSize: number;
};

let boundSend: SendFn | null = null;
let controlState: ControlState = {
  mode: "editor",
  overlay: true,
  look: 1,
  deadzone: 0.28,
  leftSize: 128,
  rightSize: 128,
};
const controlSubs = new Set<() => void>();

let viewPanZoom: ViewPanZoom = { panX: 0, panY: 0, scale: 1 };
let lastRemotePoint = { x: 0.5, y: 0.5 };
let lastOverlayTouchAt = 0;

function markOverlayTouch(pointerType: string) {
  if (pointerType !== "mouse") lastOverlayTouchAt = performance.now();
}

function applyViewTransform(el: HTMLElement | null, next: ViewPanZoom) {
  if (!el) return;
  el.style.transform =
    next.scale === 1 && next.panX === 0 && next.panY === 0
      ? ""
      : `translate(${next.panX}px, ${next.panY}px) scale(${next.scale})`;
}

let viewStage: HTMLElement | null = null;

function getViewPanZoom(): ViewPanZoom {
  return viewPanZoom;
}

export function setRemoteViewPanZoom(next: ViewPanZoom) {
  if (viewPanZoom.panX === next.panX && viewPanZoom.panY === next.panY && viewPanZoom.scale === next.scale) {
    applyViewTransform(viewStage, next);
    return;
  }
  viewPanZoom = next;
  applyViewTransform(viewStage, next);
}

export function resetRemoteViewPanZoom() {
  setRemoteViewPanZoom({ panX: 0, panY: 0, scale: 1 });
}

function bindViewStage(el: HTMLElement | null) {
  viewStage = el;
  applyViewTransform(el, viewPanZoom);
}

function bindRemoteSend(fn: SendFn | null) {
  boundSend = fn;
}

export function remoteViewSend(payload: Record<string, unknown>) {
  boundSend?.(payload);
}

function getControlState(): ControlState {
  return controlState;
}

export function setRemoteViewControls(patch: Partial<ControlState>) {
  controlState = { ...controlState, ...patch };
  controlSubs.forEach((fn) => fn());
}

export function useRemoteViewControls(): ControlState {
  return useSyncExternalStore(
    (onChange) => {
      controlSubs.add(onChange);
      return () => controlSubs.delete(onChange);
    },
    getControlState,
    getControlState,
  );
}

const COMMANDS: { key: string; label: string }[] = [
  { key: " ", label: "Space" },
  { key: "Escape", label: "Esc" },
];

const EDITOR_TAPS: { key: string; label: string }[] = [
  { key: "f", label: "F" },
  { key: "w", label: "W" },
  { key: "r", label: "R" },
  { key: "g", label: "G" },
];

function useWindowViews(enabled: boolean): WindowViewRow[] {
  const [rows, setRows] = useState<WindowViewRow[]>([]);
  useEffect(() => {
    if (!enabled) return;
    const listViews = getApi()?.list_window_views;
    if (!listViews) return;
    let live = true;
    const load = async () => {
      try {
        const next = await listViews();
        if (live && Array.isArray(next)) setRows(next);
      } catch {
        if (live) setRows([]);
      }
    };
    void load();
    const stop = setVisibleInterval(() => void load(), 4000);
    return () => {
      live = false;
      stop();
    };
  }, [enabled]);
  return rows;
}

const CONTROL_MODES: { id: ControlMode; label: string }[] = [
  { id: "editor", label: "Editor fly" },
  { id: "play", label: "Play" },
  { id: "editing", label: "Editing" },
];

const CONTROL_SLIDERS: { key: "look" | "deadzone" | "leftSize" | "rightSize"; label: string; min: number; max: number; step: number; digits: number }[] = [
  { key: "look", label: "Look", min: 0.25, max: 3, step: 0.05, digits: 2 },
  { key: "deadzone", label: "Deadzone", min: 0.1, max: 0.45, step: 0.01, digits: 2 },
  { key: "leftSize", label: "Left stick", min: 88, max: 200, step: 1, digits: 0 },
  { key: "rightSize", label: "Right stick", min: 88, max: 200, step: 1, digits: 0 },
];

/** Watching UEFN: the stick overlay's mode and sizes, in the View menu (the header shows
 *  only Ducky and View while streaming). */
function UefnControls() {
  const controls = useRemoteViewControls();
  const tap = (key: string) => {
    remoteViewSend({ type: "keydown", key });
    window.setTimeout(() => remoteViewSend({ type: "keyup", key }), 80);
  };
  return (
    <section className="view-menu-section" aria-label="UEFN controls">
      <div className="view-menu-section-title">Controls</div>
      <div className="view-menu-chips" role="group" aria-label="Controls mode">
        {CONTROL_MODES.map((mode) => (
          <button
            key={mode.id}
            type="button"
            className={`view-menu-chip${controls.mode === mode.id ? " is-selected" : ""}`}
            aria-pressed={controls.mode === mode.id}
            onClick={() => setRemoteViewControls({ mode: mode.id })}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <div className="view-menu-chips">
        <button type="button" className="view-menu-chip" onClick={() => setRemoteViewControls({ overlay: !controls.overlay })}>
          {controls.overlay ? "Hide sticks" : "Show sticks"}
        </button>
        <button type="button" className="view-menu-chip" onClick={() => resetRemoteViewPanZoom()}>
          Reset zoom
        </button>
        {COMMANDS.map((cmd) => (
          <button key={cmd.key} type="button" className="view-menu-chip" onClick={() => tap(cmd.key)}>
            {cmd.label}
          </button>
        ))}
      </div>
      {CONTROL_SLIDERS.map((slider) => (
        <label key={slider.key} className="remote-control-slider">
          <span>
            {slider.label} <b>{controls[slider.key].toFixed(slider.digits)}</b>
          </span>
          <input
            type="range"
            min={slider.min}
            max={slider.max}
            step={slider.step}
            value={controls[slider.key]}
            onChange={(ev) => setRemoteViewControls({ [slider.key]: Number(ev.target.value) })}
          />
        </label>
      ))}
    </section>
  );
}
/**
 * Browser side of Remote View: WebRTC only. The tunnel WebSocket carries
 * SDP/ICE and the window-fit size; video and input are peer-to-peer.
 * No JPEG fallback — a failed peer connection shows why and retries.
 */

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun1.l.google.com:19302" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
  bundlePolicy: "max-bundle",
  rtcpMuxPolicy: "require",
};

const CONNECT_MS = 12_000;
const RETRY_MS = [1500, 3000, 5000, 8000];
const MAX_AUTO_RETRIES = 4;
const SIZE_DEBOUNCE_MS = 400;

/** A stream nobody touches for this long pauses: the desktop stops capturing and the phone
 *  stops spending battery and data. A tap resumes it. */
export const IDLE_PAUSE_MS = 5 * 60_000;
const IDLE_CHECK_MS = 15_000;

/** True once the view has had no touch, key or scroll for IDLE_PAUSE_MS while streaming. */
export function useIdlePause(
  overlayRef: { current: HTMLElement | null },
  active: boolean,
): { idle: boolean; resume: () => void } {
  const [idle, setIdle] = useState(false);
  const lastInput = useRef(Date.now());
  useEffect(() => {
    const el = overlayRef.current;
    const mark = () => {
      lastInput.current = Date.now();
    };
    const opts = { capture: true, passive: true } as const;
    const kinds = ["pointerdown", "touchstart", "wheel", "keydown"] as const;
    for (const kind of kinds) el?.addEventListener(kind, mark, opts);
    window.addEventListener("keydown", mark, opts);
    return () => {
      for (const kind of kinds) el?.removeEventListener(kind, mark, opts);
      window.removeEventListener("keydown", mark, opts);
    };
  }, [overlayRef, active]);
  useEffect(() => {
    if (!active || idle) return;
    lastInput.current = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - lastInput.current >= IDLE_PAUSE_MS) setIdle(true);
    }, IDLE_CHECK_MS);
    return () => window.clearInterval(timer);
  }, [active, idle]);
  const resume = useCallback(() => {
    lastInput.current = Date.now();
    setIdle(false);
  }, []);
  return { idle, resume };
}

const DESKTOP_POINTER = "(hover: hover) and (pointer: fine)";

/** A mouse and a keyboard (a PC), not a phone: the picture takes keys straight, with no typing box. */
export function useDesktopPointer(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mq = window.matchMedia?.(DESKTOP_POINTER);
      mq?.addEventListener("change", onChange);
      return () => mq?.removeEventListener("change", onChange);
    },
    () => !!window.matchMedia?.(DESKTOP_POINTER).matches,
    () => false,
  );
}

/** Capture only while this tab is actually on screen — same idea as UE pausing when you tab away. */
function useViewerActive(): boolean {
  const [on, setOn] = useState(() => typeof document === "undefined" || document.visibilityState === "visible");
  useEffect(() => {
    const sync = () => setOn(document.visibilityState === "visible");
    const off = () => setOn(false);
    document.addEventListener("visibilitychange", sync);
    window.addEventListener("pagehide", off);
    window.addEventListener("pageshow", sync);
    document.addEventListener("freeze", off);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      window.removeEventListener("pagehide", off);
      window.removeEventListener("pageshow", sync);
      document.removeEventListener("freeze", off);
    };
  }, []);
  return on;
}

const LAUNCH_WAIT_MS = 120_000;

let uefnLaunchLabel = "";
const launchSubs = new Set<() => void>();

function emitLaunch() {
  launchSubs.forEach((fn) => fn());
}

function setUeFnLaunching(label: string) {
  if (uefnLaunchLabel === label) return;
  uefnLaunchLabel = label;
  emitLaunch();
}

export function useUeFnLaunching(): string {
  return useSyncExternalStore(
    (onChange) => {
      launchSubs.add(onChange);
      return () => launchSubs.delete(onChange);
    },
    () => uefnLaunchLabel,
    () => uefnLaunchLabel,
  );
}

export function RemoteWindowLaunching({ label }: { label: string }) {
  return (
    <div className="remote-window-overlay" aria-live="polite">
      <div className="remote-window-screen">
        <div className="remote-window-status">
          <DuckyParade label={`Launching ${label}…`} />
        </div>
      </div>
    </div>
  );
}

export function RemoteWindowSelect({
  value,
  onChange,
  projectName = "",
}: {
  value: string;
  onChange: (id: string) => void;
  projectName?: string;
}) {
  const { confirm, alert } = useConfirmModal();
  const [rows, setRows] = useState<WindowViewRow[]>([]);
  const [busy, setBusy] = useState(false);
  const pendingUeFn = useRef<"hub" | "project" | null>(null);
  const seenUeFn = useRef<Set<string>>(new Set());
  const waitTimer = useRef(0);

  const stopWaiting = useCallback(() => {
    pendingUeFn.current = null;
    setBusy(false);
    setUeFnLaunching("");
    if (waitTimer.current) {
      window.clearTimeout(waitTimer.current);
      waitTimer.current = 0;
    }
  }, []);

  const applyRows = useCallback(
    (next: WindowViewRow[]) => {
      const nextId = pickUeFnFollow({
        rows: next,
        seenIds: seenUeFn.current,
        pending: pendingUeFn.current,
        selectedId: value,
      });
      if (nextId !== undefined) {
        if (pendingUeFn.current && nextId) stopWaiting();
        if (nextId !== value) onChange(nextId);
      }
      seenUeFn.current = new Set(next.filter((row) => row.kind === "uefn").map((row) => row.id));
      setRows(next);
    },
    [onChange, stopWaiting, value],
  );

  const load = useCallback(async () => {
    if (!isRemote()) return;
    const listViews = getApi()?.list_window_views;
    if (!listViews) return;
    try {
      const next = await listViews();
      if (Array.isArray(next)) applyRows(next);
    } catch {
      applyRows([]);
    }
  }, [applyRows]);

  useEffect(() => {
    if (!isRemote()) return;
    void load();
    const ms = busy ? 800 : 2000;
    return setVisibleInterval(() => void load(), ms);
  }, [busy, load]);

  const runUeFn = useCallback(
    async (kind: "hub" | "project" | "restart" | "close") => {
      const api = getApi();
      const fn =
        kind === "close"
          ? api?.close_uefn
          : kind === "restart"
            ? api?.restart_uefn_project
            : kind === "project"
              ? api?.launch_uefn_project
              : api?.launch_uefn;
      if (!fn) {
        await alert("UEFN launch is unavailable on this panel.");
        return;
      }
      if (kind === "restart" || kind === "close") {
        const closing = kind === "close";
        const ok = await confirm({
          title: closing ? "Close UEFN?" : "Restart UEFN?",
          message: closing
            ? "This closes Unreal Editor for Fortnite. Unsaved editor work will be lost."
            : "This closes Unreal Editor for Fortnite and reopens the current project. Unsaved editor work will be lost.",
          confirmLabel: closing ? "Close" : "Restart",
          danger: true,
        });
        if (!ok) return;
        onChange("");
      }
      if (kind === "close") {
        try {
          await fn();
        } catch (e) {
          await alert(e instanceof Error ? e.message : String(e));
        }
        void load();
        return;
      }
      const label = kind === "hub" ? "UEFN" : projectName.trim() || "project";
      setBusy(true);
      setUeFnLaunching(label);
      pendingUeFn.current = kind === "hub" ? "hub" : "project";
      seenUeFn.current = new Set(rows.filter((row) => row.kind === "uefn").map((row) => row.id));
      if (waitTimer.current) window.clearTimeout(waitTimer.current);
      waitTimer.current = window.setTimeout(() => {
        if (!pendingUeFn.current) return;
        stopWaiting();
        void alert("UEFN didn't open a window. Check the desktop — it may still be starting.");
      }, LAUNCH_WAIT_MS);
      try {
        await fn();
      } catch (e) {
        stopWaiting();
        await alert(e instanceof Error ? e.message : String(e));
      }
      void load();
    },
    [alert, confirm, load, onChange, projectName, rows, stopWaiting],
  );

  useEffect(() => () => {
    if (waitTimer.current) window.clearTimeout(waitTimer.current);
  }, []);

  if (!isRemote()) return null;

  return (
    <div className="remote-window-select no-drag" onPointerDown={() => void load()}>
      <ViewMenu
        rows={rows}
        value={value}
        busy={busy}
        projectName={projectName.trim()}
        watchingUefn={rows.find((row) => row.id === value)?.kind === "uefn"}
        onPick={onChange}
        onUeFn={(kind) => void runUeFn(kind)}
      />
    </div>
  );
}

/** "Fleet - Google Chrome" → the window's own name, then the app's. */
function splitWindowTitle(title: string): { name: string; app: string } {
  const at = title.lastIndexOf(" - ");
  if (at <= 0 || at >= title.length - 3) return { name: title, app: "" };
  return { name: title.slice(0, at), app: title.slice(at + 3) };
}

/** "Display 2 (1920×1080)" → its label, size, and whether it is an ultrawide. */
function screenParts(row: WindowViewRow): { label: string; size: string; wide: boolean } {
  if (row.kind === "desktop") return { label: "All screens", size: "", wide: false };
  const match = /^(.*?)\s*\((\d+)×(\d+)\)$/.exec(row.title);
  if (!match) return { label: row.title, size: "", wide: false };
  const [, label, w, h] = match;
  return { label, size: `${w}×${h}`, wide: Number(w) / Math.max(1, Number(h)) > 2.2 };
}

function windowIcon(kind?: string) {
  if (kind === "uefn") return <Icons.Mountain />;
  if (kind === "blender") return <Icons.Box />;
  return <Icons.Maximize />;
}

function ViewMenuItem({
  icon,
  title,
  subtitle,
  selected,
  hero,
  onPick,
  children,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  selected: boolean;
  hero?: boolean;
  onPick: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`view-menu-item${selected ? " is-selected" : ""}${hero ? " view-menu-item--hero" : ""}`}>
      <button
        type="button"
        role="menuitemradio"
        aria-checked={selected}
        className="view-menu-item-main"
        data-view-item
        title={title}
        onClick={onPick}
      >
        <span className="view-menu-icon" aria-hidden>
          {icon}
        </span>
        <span className="view-menu-copy">
          <span className="view-menu-title">{title}</span>
          {subtitle ? <span className="view-menu-sub">{subtitle}</span> : null}
        </span>
        {selected ? (
          <span className="view-menu-check" aria-hidden>
            <Icons.Check />
          </span>
        ) : null}
      </button>
      {children}
    </div>
  );
}

/**
 * The phone/web header's View menu: Ducky itself, UEFN (launch, restart, close), whole
 * screens as tiles, and every other window with its app's name — icons and a check for
 * what is showing, not radio circles.
 */
function ViewMenu({
  rows,
  value,
  busy,
  projectName,
  watchingUefn,
  onPick,
  onUeFn,
}: {
  rows: WindowViewRow[];
  value: string;
  busy: boolean;
  projectName: string;
  watchingUefn: boolean;
  onPick: (id: string) => void;
  onUeFn: (kind: "hub" | "project" | "restart" | "close") => void;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const uefnRows = rows.filter((row) => row.kind === "uefn");
  const screenRows = rows.filter((row) => row.kind === "desktop" || row.kind === "monitor");
  const windowRows = rows.filter((row) => row.kind !== "uefn" && row.kind !== "desktop" && row.kind !== "monitor");
  const current = value ? rows.find((row) => row.id === value)?.title || "a window" : "Ducky";

  const close = () => {
    setOpen(false);
    anchorRef.current?.focus({ preventScroll: true });
  };
  const pick = (id: string) => {
    onPick(id);
    close();
  };
  const runUeFn = (kind: "hub" | "project" | "restart" | "close") => {
    onUeFn(kind);
    setOpen(false);
  };

  useLayoutEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    const target =
      menu?.querySelector<HTMLElement>('[data-view-item][aria-checked="true"]') ??
      menu?.querySelector<HTMLElement>("[data-view-item]:not(:disabled)");
    target?.focus({ preventScroll: true });
  }, [open]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    const edge = event.key === "Home" ? "first" : event.key === "End" ? "last" : "";
    if (!step && !edge) return;
    event.preventDefault();
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>("[data-view-item]:not(:disabled)") ?? [])];
    if (!items.length) return;
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next =
      edge === "first" ? 0 : edge === "last" ? items.length - 1 : (at + step + items.length) % items.length;
    items[next]?.focus();
  };

  return (
    <div className="choice-dropdown choice-dropdown--compact choice-dropdown--icon choice-dropdown--light">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="View"
        title={`View: ${current}`}
        onClick={() => setOpen((on) => !on)}
      >
        <ChoiceTriggerFace icon={<Icons.Monitor />} />
      </button>
      <DropdownPanel
        open={open}
        anchorRef={anchorRef}
        onClose={() => setOpen(false)}
        placement="bottom"
        minWidth={300}
        width={320}
        zIndex={100020}
      >
        <div
          ref={menuRef}
          className="view-menu"
          role="menu"
          aria-label="View"
          data-dropdown-keep
          onKeyDown={onKeyDown}
          onPointerDown={(event) => event.stopPropagation()}
          onWheel={(event) => event.stopPropagation()}
        >
          <ViewMenuItem
            hero
            icon={<img src="./OnlineMCPIcon.png" alt="" draggable={false} />}
            title="Ducky"
            subtitle="Chats, workflows and the editor"
            selected={!value}
            onPick={() => pick("")}
          />

          <section className="view-menu-section" aria-label="UEFN">
            <div className="view-menu-section-title">UEFN</div>
            {uefnRows.length ? (
              uefnRows.map((row) => (
                <ViewMenuItem
                  key={row.id}
                  icon={windowIcon("uefn")}
                  title={row.title}
                  subtitle={busy ? "Working…" : "Unreal Editor for Fortnite"}
                  selected={value === row.id}
                  onPick={() => pick(row.id)}
                >
                  <button
                    type="button"
                    className="view-menu-action"
                    data-view-item
                    disabled={busy}
                    title="Restart UEFN"
                    aria-label="Restart UEFN"
                    onClick={() => runUeFn("restart")}
                  >
                    <Icons.Replay />
                  </button>
                  <button
                    type="button"
                    className="view-menu-action is-danger"
                    data-view-item
                    disabled={busy}
                    title="Close UEFN"
                    aria-label="Close UEFN"
                    onClick={() => runUeFn("close")}
                  >
                    <Icons.Close />
                  </button>
                </ViewMenuItem>
              ))
            ) : (
              <div className="view-menu-launch">
                <span className="view-menu-icon" aria-hidden>
                  {windowIcon("uefn")}
                </span>
                <span className="view-menu-copy">
                  <span className="view-menu-title">{projectName || "UEFN"}</span>
                  <span className="view-menu-sub">{busy ? "Starting…" : "Not open"}</span>
                </span>
                {projectName ? (
                  <button
                    type="button"
                    className="view-menu-ghost-btn"
                    data-view-item
                    disabled={busy}
                    title="Open the UEFN hub instead of this project"
                    onClick={() => runUeFn("hub")}
                  >
                    Hub
                  </button>
                ) : null}
                <button
                  type="button"
                  className="view-menu-launch-btn"
                  data-view-item
                  disabled={busy}
                  onClick={() => runUeFn(projectName ? "project" : "hub")}
                >
                  <Icons.Play />
                  {busy ? "Starting" : "Launch"}
                </button>
              </div>
            )}
          </section>

          {watchingUefn ? <UefnControls /> : null}

          {screenRows.length ? (
            <section className="view-menu-section" aria-label="Screens">
              <div className="view-menu-section-title">Screens</div>
              <div className="view-menu-screens">
                {screenRows.map((row) => {
                  const { label, size, wide } = screenParts(row);
                  const selected = value === row.id;
                  return (
                    <button
                      key={row.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={selected}
                      className={`view-menu-screen${selected ? " is-selected" : ""}`}
                      data-view-item
                      title={row.title}
                      onClick={() => pick(row.id)}
                    >
                      <span
                        className={`view-menu-screen-glyph${row.kind === "desktop" ? " view-menu-screen-glyph--all" : ""}${wide ? " view-menu-screen-glyph--wide" : ""}`}
                        aria-hidden
                      >
                        <span />
                        {row.kind === "desktop" ? <span /> : null}
                      </span>
                      <span className="view-menu-screen-label">{label}</span>
                      {size ? <span className="view-menu-screen-sub">{size}</span> : null}
                    </button>
                  );
                })}
              </div>
            </section>
          ) : null}

          <section className="view-menu-section" aria-label="Windows">
            <div className="view-menu-section-title">
              Windows
              {windowRows.length ? <span className="view-menu-count">{windowRows.length}</span> : null}
            </div>
            {windowRows.length ? (
              windowRows.map((row) => {
                const { name, app } = splitWindowTitle(row.title);
                return (
                  <ViewMenuItem
                    key={row.id}
                    icon={windowIcon(row.kind)}
                    title={name}
                    subtitle={app}
                    selected={value === row.id}
                    onPick={() => pick(row.id)}
                  />
                );
              })
            ) : (
              <div className="view-menu-empty">No other windows are open.</div>
            )}
          </section>
        </div>
      </DropdownPanel>
    </div>
  );
}

function wsUrl(hwnd: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/__window_stream?id=${encodeURIComponent(hwnd)}`;
}

type Rect = { left: number; top: number; width: number; height: number };

function videoRect(video: HTMLVideoElement, viewport: HTMLElement): Rect {
  const r = viewport.getBoundingClientRect();
  return contentRect(
    { left: r.left, top: r.top, width: r.width, height: r.height },
    video.videoWidth,
    video.videoHeight,
    video.dataset.fill === "1",
  );
}

function norm(rect: Rect, clientX: number, clientY: number) {
  const w = rect.width || 1;
  const h = rect.height || 1;
  return {
    x: Math.min(1, Math.max(0, (clientX - rect.left) / w)),
    y: Math.min(1, Math.max(0, (clientY - rect.top) / h)),
  };
}

function overlaySize(el: HTMLElement | null): { w: number; h: number } | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 80 || r.height < 80) return null;
  const dpr = window.devicePixelRatio || 1;
  return { w: Math.round(r.width * dpr), h: Math.round(r.height * dpr) };
}

const HOLD_MS = 450;
const TEXT_FOCUS_MS = 150;
/** A finger held still on the PC needs a fresh position now and then, or Windows lets go. */
const TOUCH_KEEPALIVE_MS = 100;

function spawnRipple(host: HTMLElement, clientX: number, clientY: number, kind: "hold" | "tap"): HTMLSpanElement {
  const r = host.getBoundingClientRect();
  const el = document.createElement("span");
  el.className = kind === "tap" ? "remote-touch-ripple is-tap" : "remote-touch-ripple";
  el.style.left = `${clientX - r.left}px`;
  el.style.top = `${clientY - r.top}px`;
  host.appendChild(el);
  if (kind === "tap") window.setTimeout(() => el.remove(), 280);
  return el;
}

/**
 * Pointer/key capture on the video. A mouse is the PC's mouse: it follows the pointer
 * over the picture with no button held, and clicks, drags and scrolls there. A finger is
 * a finger on the PC (touch: the PC's pointer neither moves nor shows), like Chrome
 * Remote Desktop: tap = click, long press = double-click, slide = drag, two fingers
 * pan/zoom the local view. Keys go to the PC, held keys repeat there.
 */
function attachInput(
  video: HTMLVideoElement,
  send: (payload: Record<string, unknown>) => void,
  viewport: HTMLElement,
  overlay: HTMLElement,
  onTap?: () => void,
) {
  let pendingMove: { x: number; y: number; button: number; touch?: true } | null = null;
  let lastMove: { x: number; y: number; button: number; touch?: true } | null = null;
  let raf = 0;
  const flush = () => {
    raf = 0;
    if (!pendingMove) return;
    send({ type: "move", ...pendingMove });
    lastMove = pendingMove;
    pendingMove = null;
  };
  const viewBox = () => {
    const r = viewport.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };
  const point = (clientX: number, clientY: number) => {
    const box = viewBox();
    const vz = getViewPanZoom();
    const p = mapViewPoint(clientX, clientY, box, vz.panX, vz.panY, vz.scale);
    const n = norm(videoRect(video, viewport), p.x, p.y);
    lastRemotePoint = n;
    return n;
  };
  const onPicture = (clientX: number, clientY: number) => {
    const vz = getViewPanZoom();
    const p = mapViewPoint(clientX, clientY, viewBox(), vz.panX, vz.panY, vz.scale);
    const r = videoRect(video, viewport);
    return p.x >= r.left && p.x <= r.left + r.width && p.y >= r.top && p.y <= r.top + r.height;
  };
  const pts = new Map<number, { x: number; y: number }>();
  let holdTimer = 0;
  let holdRipple: HTMLElement | null = null;
  let pendingId = -1;
  let held = false;
  let dragId = -1;
  let dragButton = 0;
  let dragTouch = false;
  let keepAlive = 0;
  let ignoreUntilClear = false;
  let pinch: { dist: number; cx: number; cy: number } | null = null;
  let downAt = { x: 0, y: 0 };
  let downMapped = { x: 0.5, y: 0.5 };
  let lastTap: { x: number; y: number; at: number; mapped: { x: number; y: number } } | null = null;

  const capture = (id: number) => {
    try {
      video.setPointerCapture(id);
    } catch {
      /* ignore */
    }
  };
  const stopKeepAlive = () => {
    if (keepAlive) window.clearInterval(keepAlive);
    keepAlive = 0;
  };
  const clearHold = () => {
    if (holdTimer) {
      window.clearTimeout(holdTimer);
      holdTimer = 0;
    }
    pendingId = -1;
    held = false;
    holdRipple?.remove();
    holdRipple = null;
  };
  const endDrag = (clientX: number, clientY: number) => {
    if (dragId < 0) return;
    flush();
    stopKeepAlive();
    send({ type: "up", ...point(clientX, clientY), button: dragButton, ...(dragTouch ? { touch: true } : {}) });
    dragId = -1;
    dragTouch = false;
    holdRipple?.remove();
    holdRipple = null;
  };
  const tapAtDown = () => {
    spawnRipple(overlay, downAt.x, downAt.y, "tap");
    const now = performance.now();
    // A second tap lands where the first did, so the PC sees a double-click.
    const second = !!lastTap && isDoubleTap(lastTap, { x: downAt.x, y: downAt.y, at: now });
    const mapped = second && lastTap ? lastTap.mapped : downMapped;
    lastRemotePoint = mapped;
    send({ type: "down", ...mapped, button: 0, touch: true });
    send({ type: "up", ...mapped, button: 0, touch: true });
    lastTap = second ? null : { x: downAt.x, y: downAt.y, at: now, mapped };
    onTap?.();
  };
  const longPress = () => {
    send({ type: "dblclick", ...downMapped, button: 0, touch: true });
    lastTap = null;
    holdRipple?.classList.add("is-locked");
    const ripple = holdRipple;
    holdRipple = null;
    window.setTimeout(() => ripple?.remove(), 220);
  };
  const startTouchDrag = (clientX: number, clientY: number) => {
    if (pendingId < 0 || dragId >= 0) return;
    const id = pendingId;
    if (holdTimer) window.clearTimeout(holdTimer);
    holdTimer = 0;
    capture(id);
    holdRipple?.classList.add("is-locked");
    flush();
    send({ type: "down", ...downMapped, button: 0, touch: true });
    lastRemotePoint = downMapped;
    dragId = id;
    dragButton = 0;
    dragTouch = true;
    pendingId = -1;
    lastTap = null;
    pendingMove = { ...point(clientX, clientY), button: 0, touch: true };
    if (!raf) raf = requestAnimationFrame(flush);
    keepAlive = window.setInterval(() => {
      if (!pendingMove && lastMove) send({ type: "move", ...lastMove });
    }, TOUCH_KEEPALIVE_MS);
  };
  const syncPinch = () => {
    if (pts.size < 2) {
      pinch = null;
      return;
    }
    const [a, b] = [...pts.values()];
    const dist = twoPointDist(a, b);
    const c = twoPointCenter(a, b);
    if (!pinch) {
      pinch = { dist, cx: c.x, cy: c.y };
      return;
    }
    const vz = getViewPanZoom();
    const box = viewBox();
    const scaled = panZoomAround(box, vz.panX, vz.panY, vz.scale, pinchScale(pinch.dist, dist, vz.scale), c.x, c.y);
    setRemoteViewPanZoom(addViewPan(box, scaled.panX, scaled.panY, scaled.scale, c.x - pinch.cx, c.y - pinch.cy));
    pinch = { dist, cx: c.x, cy: c.y };
  };

  const onWheel = (ev: WheelEvent) => {
    ev.preventDefault();
    flush();
    send({ type: "wheel", ...point(ev.clientX, ev.clientY), delta: ev.deltaY > 0 ? 1 : -1 });
  };
  const onDown = (ev: PointerEvent) => {
    ev.preventDefault();
    if (ev.pointerType === "mouse" && ignoreMouseAfterTouch(lastOverlayTouchAt, performance.now())) return;
    video.focus({ preventScroll: true });
    // Back and forward buttons have no PC button to press.
    if (ev.pointerType === "mouse" && ev.button > 2) return;
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (ev.pointerType === "mouse") {
      capture(ev.pointerId);
      flush();
      send({ type: "down", ...point(ev.clientX, ev.clientY), button: ev.button });
      dragId = ev.pointerId;
      dragButton = ev.button;
      dragTouch = false;
      return;
    }
    if (pts.size >= 2) {
      if (dragId >= 0) endDrag(ev.clientX, ev.clientY);
      clearHold();
      ignoreUntilClear = true;
      syncPinch();
      return;
    }
    if (ignoreUntilClear) return;
    capture(ev.pointerId);
    pendingId = ev.pointerId;
    held = false;
    downAt = { x: ev.clientX, y: ev.clientY };
    downMapped = point(ev.clientX, ev.clientY);
    holdRipple = spawnRipple(overlay, ev.clientX, ev.clientY, "hold");
    holdTimer = window.setTimeout(() => {
      holdTimer = 0;
      if (pendingId !== ev.pointerId) return;
      held = true;
      longPress();
    }, HOLD_MS);
  };
  const onMove = (ev: PointerEvent) => {
    if (!pts.has(ev.pointerId)) {
      // No button held: a mouse over the picture moves the PC's mouse, while this window
      // has the keyboard (a pointer passing over it in the background moves nothing).
      if (ev.pointerType !== "mouse" || ev.buttons || dragId >= 0 || !document.hasFocus()) return;
      if (ignoreMouseAfterTouch(lastOverlayTouchAt, performance.now())) return;
      if (!onPicture(ev.clientX, ev.clientY)) return;
      pendingMove = { ...point(ev.clientX, ev.clientY), button: 0 };
      if (!raf) raf = requestAnimationFrame(flush);
      return;
    }
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pts.size >= 2) {
      syncPinch();
      return;
    }
    if (ev.pointerId === pendingId) {
      if (held) return;
      if (holdRipple) {
        const box = overlay.getBoundingClientRect();
        holdRipple.style.left = `${ev.clientX - box.left}px`;
        holdRipple.style.top = `${ev.clientY - box.top}px`;
      }
      if (!withinSlop(downAt.x, downAt.y, ev.clientX, ev.clientY)) startTouchDrag(ev.clientX, ev.clientY);
      return;
    }
    if (ev.pointerId !== dragId) return;
    if (dragTouch && holdRipple) {
      const box = overlay.getBoundingClientRect();
      holdRipple.style.left = `${ev.clientX - box.left}px`;
      holdRipple.style.top = `${ev.clientY - box.top}px`;
    }
    pendingMove = { ...point(ev.clientX, ev.clientY), button: dragButton, ...(dragTouch ? { touch: true as const } : {}) };
    if (!raf) raf = requestAnimationFrame(flush);
  };
  const onUp = (ev: PointerEvent) => {
    pts.delete(ev.pointerId);
    if (ev.pointerId === dragId) {
      endDrag(ev.clientX, ev.clientY);
    } else if (ev.pointerId === pendingId) {
      const wasHeld = held;
      clearHold();
      if (!wasHeld && !ignoreUntilClear) tapAtDown();
    }
    if (pts.size < 2) pinch = null;
    if (pts.size === 0) ignoreUntilClear = false;
  };
  // The picture has the keyboard: this app's own shortcuts stay out of it.
  const onKeyDown = (ev: KeyboardEvent) => {
    ev.preventDefault();
    ev.stopPropagation();
    send({ type: "keydown", key: ev.key });
  };
  const onKeyUp = (ev: KeyboardEvent) => {
    ev.preventDefault();
    ev.stopPropagation();
    send({ type: "keyup", key: ev.key });
  };
  const onContext = (ev: Event) => ev.preventDefault();
  // A middle click is the PC's, not this page's auto-scroll.
  const onMouseDown = (ev: MouseEvent) => {
    if (ev.button === 1) ev.preventDefault();
  };
  video.addEventListener("wheel", onWheel, { passive: false });
  video.addEventListener("pointerdown", onDown);
  video.addEventListener("pointerup", onUp);
  video.addEventListener("pointercancel", onUp);
  video.addEventListener("pointermove", onMove);
  video.addEventListener("keydown", onKeyDown);
  video.addEventListener("keyup", onKeyUp);
  video.addEventListener("contextmenu", onContext);
  video.addEventListener("mousedown", onMouseDown);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    stopKeepAlive();
    if (dragId >= 0 && dragTouch) send({ type: "up", ...lastRemotePoint, button: 0, touch: true });
    clearHold();
    video.removeEventListener("wheel", onWheel);
    video.removeEventListener("pointerdown", onDown);
    video.removeEventListener("pointerup", onUp);
    video.removeEventListener("pointercancel", onUp);
    video.removeEventListener("pointermove", onMove);
    video.removeEventListener("keydown", onKeyDown);
    video.removeEventListener("keyup", onKeyUp);
    video.removeEventListener("contextmenu", onContext);
    video.removeEventListener("mousedown", onMouseDown);
  };
}
function preferCodecs(transceiver: RTCRtpTransceiver) {
  const caps = RTCRtpReceiver.getCapabilities?.("video");
  if (!caps) return;
  try {
    transceiver.setCodecPreferences(
      [...caps.codecs].sort((a, b) => rankVideoCodec(a) - rankVideoCodec(b)),
    );
  } catch {
    /* Safari < 15.4 */
  }
}

function zeroPlayoutDelay(receiver: RTCRtpReceiver) {
  const r = receiver as RTCRtpReceiver & { jitterBufferTarget?: number; playoutDelayHint?: number };
  try {
    r.jitterBufferTarget = 0;
  } catch {
    /* ignore */
  }
  try {
    r.playoutDelayHint = 0;
  } catch {
    /* ignore */
  }
}

type Phase = "connecting" | "live" | "failed" | "kicked";
type Stats = { codec: string; w: number; h: number; fps: number; kbps: number; rtt: number };

type StatsCursor = { bytes: number; at: number };

/** The picture's numbers, and everything the link has received (`link`, for the freeze check). */
async function sampleStats(pc: RTCPeerConnection, cursor: StatsCursor): Promise<{ stats: Stats | null; link: number }> {
  const report = await pc.getStats();
  let inbound: Record<string, unknown> | null = null;
  const codecs = new Map<string, string>();
  let rtt = 0;
  let link = 0;
  report.forEach((row) => {
    const r = row as Record<string, unknown>;
    if (r.type === "inbound-rtp" && r.kind === "video") inbound = r;
    else if (r.type === "codec") codecs.set(String(r.id), String(r.mimeType || ""));
    else if (r.type === "candidate-pair" && (r.nominated || r.state === "succeeded")) {
      const v = Number(r.currentRoundTripTime || 0);
      if (v > 0) rtt = v;
      link = Math.max(link, Number(r.bytesReceived || 0));
    }
  });
  if (!inbound) return { stats: null, link };
  const row = inbound as Record<string, unknown>;
  const bytes = Number(row.bytesReceived || 0);
  const now = performance.now();
  const dt = Math.max(1, now - cursor.at) / 1000;
  const kbps = cursor.at ? ((bytes - cursor.bytes) * 8) / dt / 1000 : 0;
  cursor.bytes = bytes;
  cursor.at = now;
  const mime = codecs.get(String(row.codecId || "")) || "";
  return {
    stats: {
      codec: mime.split("/")[1] || "",
      w: Number(row.frameWidth || 0),
      h: Number(row.frameHeight || 0),
      fps: Math.round(Number(row.framesPerSecond || 0)),
      kbps: Math.round(kbps),
      rtt: Math.round(rtt * 1000),
    },
    link,
  };
}

function statsLabel(s: Stats): string {
  const parts: string[] = [];
  if (s.w && s.h) parts.push(`${s.w}×${s.h}`);
  if (s.codec) parts.push(s.codec);
  parts.push(`${s.fps} fps`);
  parts.push(s.kbps >= 1000 ? `${(s.kbps / 1000).toFixed(1)} Mb/s` : `${s.kbps} kb/s`);
  if (s.rtt) parts.push(`${s.rtt} ms`);
  return parts.join(" · ");
}

function UefnStickPad({
  className,
  onChange,
}: {
  className: string;
  onChange: (nx: number, ny: number, active: boolean) => void;
}) {
  const padRef = useRef<HTMLDivElement>(null);
  const knobRef = useRef<HTMLSpanElement>(null);

  const update = (clientX: number, clientY: number, active: boolean) => {
    const el = padRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const max = Math.max(1, r.width / 2);
    const knobPx = Math.max(12, Math.min(22, r.width * (18 / 88)));
    const travel = Math.max(8, max - knobPx);
    let nx = (clientX - (r.left + r.width / 2)) / max;
    let ny = (clientY - (r.top + r.height / 2)) / max;
    const mag = Math.hypot(nx, ny);
    if (mag > 1) {
      nx /= mag;
      ny /= mag;
    }
    if (knobRef.current) knobRef.current.style.transform = `translate(${nx * travel}px, ${ny * travel}px)`;
    onChange(nx, ny, active);
  };

  const end = (ev: ReactPointerEvent<HTMLDivElement>) => {
    ev.preventDefault();
    ev.stopPropagation();
    markOverlayTouch(ev.pointerType);
    if (knobRef.current) knobRef.current.style.transform = "";
    onChange(0, 0, false);
  };

  return (
    <div
      ref={padRef}
      className={className}
      onPointerDown={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        ev.currentTarget.setPointerCapture(ev.pointerId);
        update(ev.clientX, ev.clientY, true);
      }}
      onPointerMove={(ev) => {
        if (!ev.currentTarget.hasPointerCapture(ev.pointerId)) return;
        ev.preventDefault();
        ev.stopPropagation();
        update(ev.clientX, ev.clientY, true);
      }}
      onPointerUp={end}
      onPointerCancel={end}
    >
      <span ref={knobRef} className="remote-stick-knob" />
    </div>
  );
}

function holdSend(send: SendFn, key: string, down: boolean) {
  send({ type: down ? "keydown" : "keyup", key });
}

function tapSend(send: SendFn, key: string) {
  send({ type: "keydown", key });
  window.setTimeout(() => send({ type: "keyup", key }), 80);
}

function tapChord(send: SendFn, keys: string[]) {
  for (const ev of chordTap(keys)) send(ev);
}

function StickBtn({
  label,
  onDown,
  onUp,
}: {
  label: string;
  onDown: () => void;
  onUp?: () => void;
}) {
  return (
    <button
      type="button"
      className="remote-stick-btn"
      onPointerDown={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        onDown();
      }}
      onPointerUp={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        markOverlayTouch(ev.pointerType);
        onUp?.();
      }}
      onPointerCancel={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        markOverlayTouch(ev.pointerType);
        onUp?.();
      }}
    >
      {label}
    </button>
  );
}

function UefnStickOverlay({
  send,
  mode,
  look,
  deadzone,
  leftSize,
  rightSize,
}: {
  send: SendFn;
  mode: ControlMode;
  look: number;
  deadzone: number;
  leftSize: number;
  rightSize: number;
}) {
  const moveHeld = useRef<string[]>([]);
  const looking = useRef(false);
  const lookVec = useRef({ nx: 0, ny: 0 });
  const lookRaf = useRef(0);
  const mousing = useRef(false);
  const mouseVec = useRef({ nx: 0, ny: 0 });
  const mouseRaf = useRef(0);
  const lookTune = useRef({ look, deadzone });
  lookTune.current = { look, deadzone };
  const sticksRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = sticksRef.current;
    if (!el) return;
    el.style.setProperty("--stick-left", `${leftSize}px`);
    el.style.setProperty("--stick-right", `${rightSize}px`);
  }, [leftSize, rightSize]);

  const cancelLook = useCallback(() => {
    if (lookRaf.current) cancelAnimationFrame(lookRaf.current);
    lookRaf.current = 0;
    if (!looking.current) return;
    looking.current = false;
    send(buttonOnly("up", 2));
  }, [send]);

  const cancelMouse = useCallback(() => {
    if (mouseRaf.current) cancelAnimationFrame(mouseRaf.current);
    mouseRaf.current = 0;
    if (!mousing.current) return;
    mousing.current = false;
    send({ type: "up", x: lastRemotePoint.x, y: lastRemotePoint.y, button: 0 });
  }, [send]);

  const releaseAll = useCallback(() => {
    for (const key of moveHeld.current) send({ type: "keyup", key });
    moveHeld.current = [];
    cancelLook();
    cancelMouse();
  }, [send, cancelLook, cancelMouse]);

  useEffect(() => () => releaseAll(), [releaseAll, mode]);

  const onMove = (nx: number, ny: number, active: boolean) => {
    const next = active ? stickMoveKeys(nx, ny, lookTune.current.deadzone) : [];
    const diff = keyDiff(moveHeld.current, next);
    moveHeld.current = next;
    for (const key of diff.down) send({ type: "keydown", key });
    for (const key of diff.up) send({ type: "keyup", key });
  };

  const onLook = (nx: number, ny: number, active: boolean) => {
    lookVec.current = { nx, ny };
    if (!active) {
      cancelLook();
      return;
    }
    if (!looking.current) {
      looking.current = true;
      send(buttonOnly("up", 0));
      send(buttonOnly("down", 2));
    }
    if (lookRaf.current) return;
    const tick = () => {
      if (!looking.current) {
        lookRaf.current = 0;
        return;
      }
      const { dx, dy } = stickLookDelta(
        lookVec.current.nx,
        lookVec.current.ny,
        lookTune.current.look,
        lookTune.current.deadzone,
      );
      if (dx || dy) send({ type: "move", dx, dy });
      lookRaf.current = requestAnimationFrame(tick);
    };
    lookRaf.current = requestAnimationFrame(tick);
  };

  const onMousePad = (nx: number, ny: number, active: boolean) => {
    mouseVec.current = { nx, ny };
    if (!active) {
      cancelMouse();
      return;
    }
    if (!mousing.current) {
      mousing.current = true;
      send({ type: "move", ...lastRemotePoint });
      send({ type: "down", ...lastRemotePoint, button: 0 });
    }
    if (mouseRaf.current) return;
    const tick = () => {
      if (!mousing.current) {
        mouseRaf.current = 0;
        return;
      }
      const { dx, dy } = stickLookDelta(
        mouseVec.current.nx,
        mouseVec.current.ny,
        lookTune.current.look,
        lookTune.current.deadzone,
      );
      if (dx || dy) send({ type: "move", dx, dy });
      mouseRaf.current = requestAnimationFrame(tick);
    };
    mouseRaf.current = requestAnimationFrame(tick);
  };

  const clickTap = () => {
    const p = lastRemotePoint;
    send({ type: "down", ...p, button: 0 });
    window.setTimeout(() => send({ type: "up", ...p, button: 0 }), 80);
  };

  return (
    <div className="remote-sticks" ref={sticksRef}>
      <UefnStickPad className="remote-stick remote-stick--left" onChange={onMove} />
      <UefnStickPad className="remote-stick remote-stick--right" onChange={onLook} />
      {mode === "editing" ? (
        <>
          <UefnStickPad className="remote-stick remote-stick--mouse" onChange={onMousePad} />
          <div className="remote-stick-btns remote-stick-btns--mouse">
            <StickBtn label="Click" onDown={clickTap} />
          </div>
        </>
      ) : null}
      {mode === "play" ? (
        <div className="remote-stick-btns remote-stick-btns--right">
          <StickBtn
            label="Jump"
            onDown={() => holdSend(send, " ", true)}
            onUp={() => holdSend(send, " ", false)}
          />
        </div>
      ) : (
        <>
          <div className="remote-stick-btns remote-stick-btns--left">
            <StickBtn
              label="Q"
              onDown={() => holdSend(send, "q", true)}
              onUp={() => holdSend(send, "q", false)}
            />
            <StickBtn
              label="E"
              onDown={() => holdSend(send, "e", true)}
              onUp={() => holdSend(send, "e", false)}
            />
            {EDITOR_TAPS.map((cmd) => (
              <StickBtn key={cmd.key} label={cmd.label} onDown={() => tapSend(send, cmd.key)} />
            ))}
          </div>
          <div className="remote-stick-btns remote-stick-btns--right">
            <StickBtn label="Undo" onDown={() => tapChord(send, ["Control", "z"])} />
            <StickBtn label="Redo" onDown={() => tapChord(send, ["Control", "y"])} />
          </div>
        </>
      )}
    </div>
  );
}

const TYPE_MAX_PX = 112;

type TypeBarHandle = { focusFromTap: () => void; blur: () => void };

/**
 * Always under the stream, like our chat box: what you type goes to the PC on Send.
 * Send with the box empty presses Enter there; Backspace in the empty box deletes there.
 */
const RemoteTypeBar = forwardRef<TypeBarHandle, { send: SendFn; disabled: boolean }>(function RemoteTypeBar({ send, disabled }, ref) {
  const barRef = useRef<HTMLFormElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const shiftEnter = useRef(false);
  const [text, setText] = useState("");

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TYPE_MAX_PX)}px`;
  }, [text]);

  const submit = useCallback(() => {
    const value = (inputRef.current?.value ?? "").slice(0, 2000);
    setText("");
    if (value.trim()) send({ type: "text", text: value });
    else tapSend(send, "Enter");
  }, [send]);

  useImperativeHandle(
    ref,
    () => ({
      focusFromTap: () => inputRef.current?.focus({ preventScroll: true }),
      blur: () => inputRef.current?.blur(),
    }),
    [],
  );

  // A phone keyboard's Enter, even mid-word, sends instead of starting a new line.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const onBeforeInput = (ev: InputEvent) => {
      if (ev.inputType !== "insertLineBreak" && ev.inputType !== "insertParagraph") return;
      if (shiftEnter.current) return;
      ev.preventDefault();
      submit();
    };
    el.addEventListener("beforeinput", onBeforeInput);
    return () => el.removeEventListener("beforeinput", onBeforeInput);
  }, [submit]);

  // Over the phone keyboard: the box rides up over the stream instead of resizing it.
  useEffect(() => {
    const vv = window.visualViewport;
    const sync = () => {
      const el = barRef.current;
      if (!el) return;
      const gap = vv ? Math.max(0, window.innerHeight - vv.height - vv.offsetTop) : 0;
      el.style.setProperty("--remote-kb", `${gap}px`);
    };
    sync();
    vv?.addEventListener("resize", sync);
    vv?.addEventListener("scroll", sync);
    window.addEventListener("resize", sync);
    return () => {
      vv?.removeEventListener("resize", sync);
      vv?.removeEventListener("scroll", sync);
      window.removeEventListener("resize", sync);
    };
  }, []);

  const hasText = !!text.trim();
  return (
    <form
      ref={barRef}
      className="remote-type-bar"
      onSubmit={(ev) => {
        ev.preventDefault();
        submit();
      }}
      onPointerDown={(ev) => ev.stopPropagation()}
    >
      <div className="remote-type-box">
        <textarea
          ref={inputRef}
          className="remote-type-input"
          rows={1}
          value={text}
          disabled={disabled}
          placeholder="Type on the PC"
          aria-label="Type on the PC"
          enterKeyHint="send"
          autoComplete="off"
          onChange={(ev) => setText(ev.target.value)}
          onKeyDown={(ev) => {
            shiftEnter.current = ev.key === "Enter" && ev.shiftKey;
            const native = ev.nativeEvent;
            if (native.isComposing || native.keyCode === 229) return;
            if (ev.key === "Enter" && !ev.shiftKey) {
              ev.preventDefault();
              submit();
            } else if (ev.key === "Backspace" && !ev.currentTarget.value) {
              ev.preventDefault();
              tapSend(send, "Backspace");
            }
          }}
        />
        <button
          type="submit"
          className={`remote-type-send${hasText ? " has-text" : ""}`}
          disabled={disabled}
          aria-label={hasText ? "Send to the PC" : "Press Enter on the PC"}
          title={hasText ? "Send to the PC" : "Press Enter on the PC"}
          // Keeps the box focused, so the phone keyboard stays up.
          onPointerDown={(ev) => ev.preventDefault()}
        >
          {hasText ? <Icons.Send /> : <Icons.Enter />}
        </button>
      </div>
    </form>
  );
});

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const screenRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const phaseRef = useRef<Phase>("connecting");
  phaseRef.current = phase;
  const [reason, setReason] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const attemptRef = useRef(0);
  // Failures in a row; a stream that went live starts counting again.
  const streakRef = useRef(0);
  const typeBarRef = useRef<TypeBarHandle>(null);
  const tapGen = useRef(0);
  const controls = useRemoteViewControls();
  const rows = useWindowViews(!!hwnd);
  const kind = rows.find((r) => r.id === hwnd)?.kind;
  const kindRef = useRef(kind);
  if (kind) kindRef.current = kind;
  const watchingUefn = (kind || kindRef.current) === "uefn";
  const onScreen = useViewerActive();
  const desktop = useDesktopPointer();
  const { idle, resume } = useIdlePause(overlayRef, !!hwnd && onScreen);
  // Stream only while the page is on screen and someone has used it in the last few minutes.
  const viewing = onScreen && !idle;

  const send = useCallback((payload: Record<string, unknown>) => {
    const direct = getDirectTransport();
    if (direct && direct.sendInput(payload)) return;
    const text = JSON.stringify(payload);
    const dc = dcRef.current;
    if (dc && dc.readyState === "open") {
      dc.send(text);
      return;
    }
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(text);
  }, []);

  const retry = useCallback(() => {
    attemptRef.current += 1;
    setAttempt(attemptRef.current);
  }, []);

  const retryNow = useCallback(() => {
    streakRef.current = 0;
    retry();
  }, [retry]);

  const onStreamTap = useCallback(() => {
    const gen = ++tapGen.current;
    window.setTimeout(() => {
      if (gen !== tapGen.current) return;
      const probe = getApi()?.window_text_focus;
      if (!probe) return;
      void Promise.resolve(probe(hwnd))
        .then((focused) => {
          if (gen !== tapGen.current || !focused) return;
          typeBarRef.current?.focusFromTap();
        })
        .catch(() => {});
    }, TEXT_FOCUS_MS);
  }, [hwnd]);

  useEffect(() => {
    attemptRef.current = 0;
    streakRef.current = 0;
    setAttempt(0);
    resetRemoteViewPanZoom();
    typeBarRef.current?.blur();
  }, [hwnd]);

  // The first picture, not the connection, ends the loading screen — and an empty video is
  // never on screen, so a browser's own play button never shows.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const check = () => {
      const frame = video.videoWidth > 0 && video.readyState >= 2;
      setHasFrame(frame);
      setRemotePicture(frame ? { w: video.videoWidth, h: video.videoHeight } : null);
    };
    const kinds = ["loadeddata", "playing", "resize", "emptied"] as const;
    for (const k of kinds) video.addEventListener(k, check);
    return () => {
      for (const k of kinds) video.removeEventListener(k, check);
      setRemotePicture(null);
    };
  }, []);

  // A window its app won't make as narrow as the phone fills the height, trimming its sides
  // (data-fill: React owns the video's className). A whole screen always shows whole.
  useEffect(() => {
    const video = videoRef.current;
    const view = viewRef.current;
    if (!video || !view) return;
    const isWindow = /^\d+$/.test(hwnd);
    const fit = () => {
      const r = view.getBoundingClientRect();
      const box = { left: 0, top: 0, width: r.width, height: r.height };
      if (isWindow && fillsHeight(box, video.videoWidth, video.videoHeight)) video.dataset.fill = "1";
      else delete video.dataset.fill;
    };
    fit();
    video.addEventListener("resize", fit);
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    ro?.observe(view);
    return () => {
      video.removeEventListener("resize", fit);
      ro?.disconnect();
    };
  }, [hwnd]);

  // Back on a network (Wi-Fi to mobile data, out of a tunnel): dial straight away.
  useEffect(() => {
    const online = () => {
      if (phaseRef.current !== "live") retryNow();
    };
    window.addEventListener("online", online);
    return () => window.removeEventListener("online", online);
  }, [retryNow]);

  useEffect(() => {
    if (!hwnd || !viewing) return;
    let closed = false;
    let live = false;
    let retryTimer = 0;
    // Declared before fail(): the direct path calls it without ever setting a timer.
    let connectTimer = 0;
    setPhase("connecting");
    setReason(streakRef.current ? "Reconnecting…" : "");
    setStats(null);
    setHasFrame(false);

    const fail = (why: string, kind: Phase = "failed") => {
      if (closed) return;
      closed = true;
      window.clearTimeout(connectTimer);
      // A few automatic retries in a row (the ducks keep running), then stop and say why:
      // a viewer that retries forever makes the desktop re-capture and re-raise the window
      // on every attempt.
      if (kind === "failed" && streakRef.current < MAX_AUTO_RETRIES) {
        const delay = RETRY_MS[Math.min(streakRef.current, RETRY_MS.length - 1)];
        streakRef.current += 1;
        setPhase("connecting");
        setReason("Reconnecting…");
        retryTimer = window.setTimeout(retry, delay);
        return;
      }
      setPhase(kind);
      setReason(why);
    };
    const wentLive = () => {
      live = true;
      streakRef.current = 0;
      setPhase("live");
      setReason("");
    };

    // Direct (tunnel-free) mode: the transport already owns the peer
    // connection; ask the desktop to attach the cropped window track.
    const direct = getDirectTransport();
    if (direct) {
      const video = videoRef.current;
      if (direct.status.state === "failed") direct.retry();
      const unsubVideo = direct.onVideo((stream) => {
        if (!video || closed) return;
        video.srcObject = stream;
        if (stream) void video.play().catch(() => {});
      });
      const unsubStatus = direct.onStatus((st) => {
        if (closed) return;
        if (st.state === "failed") fail(st.reason || "Desktop connection lost.");
        // The link dropped under a live picture: start over, so the watch is asked again
        // once the transport has redialed (the desktop's old session is gone).
        else if (st.state === "reconnecting" && live) fail("Reconnecting…");
      });
      void direct.invoke("watch_window", [{ hwnd }]).then(
        () => {
          if (closed) return;
          wentLive();
          for (const r of direct.peer?.getReceivers() ?? []) zeroPlayoutDelay(r);
          const size = overlaySize(screenRef.current);
          if (size) direct.sendInput({ type: "size", ...size });
        },
        (err: unknown) => fail(`Desktop could not start screen capture: ${String((err as Error)?.message || err)}`),
      );
      const cursor: StatsCursor = { bytes: 0, at: 0 };
      const watch = newLinkWatch();
      const statsTimer = window.setInterval(() => {
        const pc = direct.peer;
        if (!pc || !live) return;
        void sampleStats(pc, cursor).then(({ stats: st, link }) => {
          if (closed) return;
          if (st) setStats(st);
          if (linkFrozen(watch, { bytes: link, state: pc.connectionState }, performance.now())) {
            direct.restart("The stream froze. Reconnecting…");
          }
        });
      }, 1000);
      return () => {
        closed = true;
        window.clearTimeout(retryTimer);
        window.clearInterval(statsTimer);
        unsubVideo();
        unsubStatus();
        if (video) video.srcObject = null;
        void direct.invoke("watch_window", [{ hwnd: "" }]).catch(() => {});
      };
    }

    const ws = new WebSocket(wsUrl(hwnd));
    wsRef.current = ws;
    const pc = new RTCPeerConnection(STUN);
    const transceiver = pc.addTransceiver("video", { direction: "recvonly" });
    preferCodecs(transceiver);
    const dc = pc.createDataChannel("input", { ordered: true });
    dcRef.current = dc;
    connectTimer = window.setTimeout(
      () => fail("No peer-to-peer path in time (network may block UDP)."),
      CONNECT_MS,
    );
    const sendWs = (payload: Record<string, unknown>) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(payload));
    };
    pc.onicecandidate = (ev) => {
      if (!ev.candidate) return;
      sendWs({ type: "rtc", candidate: ev.candidate.toJSON() });
    };
    pc.ontrack = (ev) => {
      const video = videoRef.current;
      if (!video) return;
      zeroPlayoutDelay(ev.receiver);
      video.srcObject = ev.streams[0] ?? new MediaStream(ev.track ? [ev.track] : []);
      void video.play().catch(() => {});
    };
    // Trickle candidates can overtake the answer through the tunnel; hold them
    // until setRemoteDescription resolves or addIceCandidate rejects them.
    let haveAnswer = false;
    const pendingIce: RTCIceCandidateInit[] = [];
    const addIce = (c: RTCIceCandidateInit) => {
      if (!haveAnswer) {
        pendingIce.push(c);
        return;
      }
      void pc.addIceCandidate(c).catch((err: unknown) => console.debug("[remote-view] addIceCandidate", err));
    };
    pc.oniceconnectionstatechange = () => console.debug("[remote-view] ice", pc.iceConnectionState);
    pc.onicegatheringstatechange = () => console.debug("[remote-view] gathering", pc.iceGatheringState);
    pc.onconnectionstatechange = () => {
      const st = pc.connectionState;
      console.debug("[remote-view] connection", st);
      if (st === "connected") {
        window.clearTimeout(connectTimer);
        wentLive();
        return;
      }
      if (st === "failed") fail(live ? "Peer connection dropped." : "Peer connection failed (network may block UDP).");
    };
    ws.onopen = () => {
      const size = overlaySize(screenRef.current);
      if (size) sendWs({ type: "size", ...size });
      void (async () => {
        try {
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          sendWs({ type: "rtc", sdp: pc.localDescription });
        } catch (err) {
          fail(`Could not create offer: ${String((err as Error)?.message || err)}`);
        }
      })();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      try {
        const msg = JSON.parse(ev.data) as {
          type?: string;
          kind?: string;
          stage?: string;
          error?: string;
          sdp?: RTCSessionDescriptionInit;
          candidate?: RTCIceCandidateInit;
        };
        if (msg.kind === "kicked") {
          fail("Another browser took over this view.", "kicked");
          return;
        }
        if (msg.kind === "fail") {
          const stage = msg.stage === "negotiate" ? "Desktop could not negotiate the stream" : "Desktop could not start screen capture";
          fail(msg.error ? `${stage}: ${msg.error}` : `${stage}.`);
          return;
        }
        if (msg.kind === "close") {
          fail("Desktop closed the stream.");
          return;
        }
        if (msg.sdp) {
          void pc.setRemoteDescription(msg.sdp).then(
            () => {
              haveAnswer = true;
              for (const c of pendingIce.splice(0)) addIce(c);
            },
            (err: unknown) => fail(`Bad answer from desktop: ${String((err as Error)?.message || err)}`),
          );
        } else if (msg.candidate) {
          addIce(msg.candidate);
        }
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      if (!live) fail("Signaling channel closed before the stream started.");
    };
    ws.onerror = () => {
      if (!live) fail("Signaling channel error.");
    };

    const cursor: StatsCursor = { bytes: 0, at: 0 };
    const watch = newLinkWatch();
    const statsTimer = window.setInterval(() => {
      if (!live) return;
      void sampleStats(pc, cursor).then(({ stats: s, link }) => {
        if (closed) return;
        if (s) setStats(s);
        if (linkFrozen(watch, { bytes: link, state: pc.connectionState }, performance.now())) {
          fail("The stream froze. Reconnecting…");
        }
      });
    }, 1000);

    return () => {
      closed = true;
      window.clearTimeout(connectTimer);
      window.clearTimeout(retryTimer);
      window.clearInterval(statsTimer);
      wsRef.current = null;
      dcRef.current = null;
      for (const close of [() => dc.close(), () => pc.close(), () => ws.close()]) {
        try {
          close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [hwnd, attempt, retry, viewing]);

  useEffect(() => {
    const el = screenRef.current;
    if (!el || !hwnd) return;
    let timer = 0;
    const ro = new ResizeObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const size = overlaySize(el);
        if (size) send({ type: "size", ...size });
        const view = viewRef.current;
        if (!view) return;
        const r = view.getBoundingClientRect();
        const vz = getViewPanZoom();
        setRemoteViewPanZoom(clampView({ left: r.left, top: r.top, width: r.width, height: r.height }, vz.panX, vz.panY, vz.scale));
      }, SIZE_DEBOUNCE_MS);
    });
    ro.observe(el);
    return () => {
      window.clearTimeout(timer);
      ro.disconnect();
    };
  }, [hwnd, attempt, send]);

  useEffect(() => {
    const el = videoRef.current;
    const view = viewRef.current;
    const overlay = overlayRef.current;
    if (!el || !view || !overlay || !hwnd || phase !== "live") return;
    // On a PC the keyboard types there straight, so a text field there needs no typing box.
    return attachInput(el, send, view, overlay, desktop ? undefined : onStreamTap);
  }, [hwnd, phase, attempt, send, onStreamTap, desktop]);

  useEffect(() => {
    bindRemoteSend(phase === "live" ? send : null);
    return () => bindRemoteSend(null);
  }, [phase, send]);

  const showing = !!hwnd && !idle && phase === "live" && hasFrame;
  // On a PC the picture has the keyboard as soon as it shows.
  useEffect(() => {
    if (desktop && showing) videoRef.current?.focus({ preventScroll: true });
  }, [desktop, showing]);

  if (!hwnd) return null;

  const loading = !idle && (phase === "connecting" || (phase === "live" && !hasFrame));

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      <div className="remote-window-screen" ref={screenRef}>
        <div className="remote-window-view" ref={viewRef}>
          <div
            className="remote-window-stage"
            ref={(el) => {
              bindViewStage(el);
            }}
          >
            <video
              ref={videoRef}
              className={showing ? "remote-window-canvas" : "remote-window-canvas is-offscreen"}
              autoPlay
              muted
              playsInline
              disablePictureInPicture
              tabIndex={0}
            />
          </div>
        </div>
        {idle ? (
          <div className="remote-window-status">
            <p className="remote-window-overlay-msg">Paused after 5 minutes without use, to save battery and data.</p>
            <button type="button" className="remote-window-retry" onClick={resume}>
              Resume
            </button>
          </div>
        ) : loading ? (
          <div className="remote-window-status" aria-live="polite">
            <DuckyParade label={phase === "live" ? "Starting the picture…" : reason || "Connecting to the PC…"} />
          </div>
        ) : phase !== "live" ? (
          <div className="remote-window-status">
            <p className="remote-window-overlay-msg">{reason}</p>
            <button type="button" className="remote-window-retry" onClick={retryNow}>
              {phase === "kicked" ? "Take over" : "Retry now"}
            </button>
          </div>
        ) : null}
        {showing && stats ? <span className="remote-window-stats">{statsLabel(stats)}</span> : null}
        {showing && controls.overlay && watchingUefn ? (
          <UefnStickOverlay
            send={send}
            mode={controls.mode}
            look={controls.look}
            deadzone={controls.deadzone}
            leftSize={controls.leftSize}
            rightSize={controls.rightSize}
          />
        ) : null}
      </div>
      {!idle && !desktop ? <RemoteTypeBar ref={typeBarRef} send={send} disabled={phase !== "live"} /> : null}
    </div>
  );
}
