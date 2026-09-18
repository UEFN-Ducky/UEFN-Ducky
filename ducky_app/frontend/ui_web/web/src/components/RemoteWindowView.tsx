import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceDropdown, ChoiceTriggerFace } from "./ChoiceDropdown";
import { DropdownPanel } from "./DropdownPanel";
import { Icons } from "../icons/Icons";
import {
  addViewPan,
  clampView,
  contentRect,
  isDoubleTap,
  keyDiff,
  mapViewPoint,
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
    const id = window.setInterval(() => void load(), 4000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [enabled]);
  return rows;
}

export function RemoteViewControls({ hwnd }: { hwnd: string }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const controls = useRemoteViewControls();
  const rows = useWindowViews(isRemote() && !!hwnd);
  const row = rows.find((r) => r.id === hwnd);
  if (!isRemote() || !hwnd || row?.kind !== "uefn") return null;

  const tap = (key: string) => {
    remoteViewSend({ type: "keydown", key });
    window.setTimeout(() => remoteViewSend({ type: "keyup", key }), 80);
  };

  return (
    <div className="remote-view-controls no-drag choice-dropdown choice-dropdown--compact choice-dropdown--icon choice-dropdown--light">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Controls"
        title="Controls"
        onClick={() => setOpen((v) => !v)}
      >
        <ChoiceTriggerFace icon={<Icons.Play />} />
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} minWidth={220}>
        <div className="plugin-header-menu-list" role="menu">
          <button
            type="button"
            className={`plugin-header-menu-item${controls.mode === "editor" ? " is-active" : ""}`}
            onClick={() => setRemoteViewControls({ mode: "editor" })}
          >
            Editor fly
          </button>
          <button
            type="button"
            className={`plugin-header-menu-item${controls.mode === "play" ? " is-active" : ""}`}
            onClick={() => setRemoteViewControls({ mode: "play" })}
          >
            Play
          </button>
          <button
            type="button"
            className={`plugin-header-menu-item${controls.mode === "editing" ? " is-active" : ""}`}
            onClick={() => setRemoteViewControls({ mode: "editing" })}
          >
            Editing
          </button>
          <button
            type="button"
            className="plugin-header-menu-item"
            onClick={() => setRemoteViewControls({ overlay: !controls.overlay })}
          >
            {controls.overlay ? "Hide sticks" : "Show sticks"}
          </button>
          <button type="button" className="plugin-header-menu-item" onClick={() => resetRemoteViewPanZoom()}>
            Reset view
          </button>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Look {controls.look.toFixed(2)}</span>
            <input
              type="range"
              min={0.25}
              max={3}
              step={0.05}
              value={controls.look}
              onChange={(ev) => setRemoteViewControls({ look: Number(ev.target.value) })}
            />
          </label>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Deadzone {controls.deadzone.toFixed(2)}</span>
            <input
              type="range"
              min={0.1}
              max={0.45}
              step={0.01}
              value={controls.deadzone}
              onChange={(ev) => setRemoteViewControls({ deadzone: Number(ev.target.value) })}
            />
          </label>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Left {Math.round(controls.leftSize)}</span>
            <input
              type="range"
              min={88}
              max={200}
              step={1}
              value={controls.leftSize}
              onChange={(ev) => setRemoteViewControls({ leftSize: Number(ev.target.value) })}
            />
          </label>
          <label
            className="remote-control-slider"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => ev.stopPropagation()}
          >
            <span>Right {Math.round(controls.rightSize)}</span>
            <input
              type="range"
              min={88}
              max={200}
              step={1}
              value={controls.rightSize}
              onChange={(ev) => setRemoteViewControls({ rightSize: Number(ev.target.value) })}
            />
          </label>
          {COMMANDS.map((cmd) => (
            <button
              key={cmd.key}
              type="button"
              className="plugin-header-menu-item"
              onClick={() => tap(cmd.key)}
            >
              {cmd.label}
            </button>
          ))}
        </div>
      </DropdownPanel>
    </div>
  );
}

/**
 * Browser side of Remote View: WebRTC only. The tunnel WebSocket carries
 * SDP/ICE and the window-fit size; video and input are peer-to-peer.
 * No JPEG fallback — a failed peer connection shows why and retries.
 */

const STUN: RTCConfiguration = {
  iceServers: [
    { urls: "stun:stun.cloudflare.com:3478" },
    { urls: "stun:stun.l.google.com:19302" },
  ],
  bundlePolicy: "max-bundle",
  rtcpMuxPolicy: "require",
};

const CONNECT_MS = 12_000;
const RETRY_MS = [1500, 3000, 5000, 8000];
const MAX_AUTO_RETRIES = 4;
const SIZE_DEBOUNCE_MS = 400;

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

const LAUNCH_UEFN_VALUE = "__launch_uefn__";
const CLOSE_UEFN_VALUE = "__close_uefn__";
const LAUNCH_WAIT_MS = 120_000;
const SKIP_VIEW_IDS = new Set([LAUNCH_UEFN_VALUE, CLOSE_UEFN_VALUE]);

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
    <div className="remote-window-overlay" role="status" aria-live="polite">
      <div className="remote-window-status">
        <span className="remote-window-spinner" aria-hidden />
        <p className="remote-window-overlay-msg">Launching {label}…</p>
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
    const id = window.setInterval(() => void load(), ms);
    return () => window.clearInterval(id);
  }, [busy, load]);

  const runUeFn = useCallback(
    async (kind: "hub" | "restart" | "close") => {
      const api = getApi();
      const fn =
        kind === "close"
          ? api?.close_uefn
          : kind === "restart"
            ? api?.restart_uefn_project
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

  const uefnRows = rows.filter((row) => row.kind === "uefn");
  const otherRows = rows.filter((row) => row.kind !== "uefn");
  const uefnOpen = uefnRows.length > 0;

  return (
    <div className="remote-window-select no-drag" onPointerDown={() => void load()}>
      <ChoiceDropdown
        size="compact"
        accordion
        className="choice-dropdown--buttons"
        aria-label="View"
        value={value}
        minWidth={280}
        placeholder="View"
        icon={<Icons.Monitor />}
        onChange={(id) => {
          if (SKIP_VIEW_IDS.has(id)) return;
          onChange(id);
        }}
        options={[
          { value: "", label: "Ducky" },
          ...(uefnOpen
            ? [
                {
                  value: CLOSE_UEFN_VALUE,
                  label: "Close UEFN",
                  group: "UEFN windows",
                  disabled: true,
                  action: {
                    label: busy ? "…" : "Close",
                    danger: true,
                    onClick: () => void runUeFn("close"),
                  },
                },
                ...uefnRows.map((row) => ({
                  value: row.id,
                  label: row.title,
                  group: "UEFN windows",
                  action: {
                    label: busy ? "…" : "Restart",
                    onClick: () => void runUeFn("restart"),
                  },
                })),
              ]
            : [
                {
                  value: LAUNCH_UEFN_VALUE,
                  label: "Launch UEFN",
                  group: "UEFN windows",
                  disabled: true,
                  action: {
                    label: busy ? "…" : "Launch",
                    onClick: () => void runUeFn("hub"),
                  },
                },
              ]),
          ...otherRows.map((row) => ({
            value: row.id,
            label: row.title,
            group: "This desktop",
          })),
        ]}
      />
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
 * Pointer/key capture on the video. Mouse stays immediate click/drag.
 * Touch: tap at the down pixel (slop ignores jitter), double-tap = dblclick,
 * hold then move past slop = drag, two fingers pan/zoom the local view.
 */
function attachInput(
  video: HTMLVideoElement,
  send: (payload: Record<string, unknown>) => void,
  viewport: HTMLElement,
  overlay: HTMLElement,
) {
  let pendingMove: { x: number; y: number; button: number } | null = null;
  let raf = 0;
  const flush = () => {
    raf = 0;
    if (!pendingMove) return;
    send({ type: "move", ...pendingMove });
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
  const pts = new Map<number, { x: number; y: number }>();
  let holdTimer = 0;
  let holdRipple: HTMLElement | null = null;
  let pendingId = -1;
  let held = false;
  let dragId = -1;
  let dragButton = 0;
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
    send({ type: "up", ...point(clientX, clientY), button: dragButton });
    dragId = -1;
    holdRipple?.remove();
    holdRipple = null;
  };
  const tapAtDown = () => {
    spawnRipple(overlay, downAt.x, downAt.y, "tap");
    const now = performance.now();
    const mapped = downMapped;
    lastRemotePoint = mapped;
    const prev = lastTap;
    if (prev && isDoubleTap(prev, { x: downAt.x, y: downAt.y, at: now })) {
      send({ type: "dblclick", ...prev.mapped, button: 0 });
      lastTap = null;
      return;
    }
    send({ type: "down", ...mapped, button: 0 });
    send({ type: "up", ...mapped, button: 0 });
    lastTap = { x: downAt.x, y: downAt.y, at: now, mapped };
  };
  const startDrag = () => {
    if (pendingId < 0 || dragId >= 0) return;
    capture(pendingId);
    holdRipple?.classList.add("is-locked");
    flush();
    send({ type: "down", ...downMapped, button: 0 });
    lastRemotePoint = downMapped;
    dragId = pendingId;
    dragButton = 0;
    pendingId = -1;
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
    video.focus({ preventScroll: true });
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    const mouse = ev.pointerType === "mouse";
    if (mouse) {
      capture(ev.pointerId);
      flush();
      send({ type: "down", ...point(ev.clientX, ev.clientY), button: ev.button });
      dragId = ev.pointerId;
      dragButton = ev.button;
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
      holdRipple?.classList.add("is-locked");
    }, HOLD_MS);
  };
  const onMove = (ev: PointerEvent) => {
    if (!pts.has(ev.pointerId)) return;
    pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pts.size >= 2) {
      syncPinch();
      return;
    }
    if (ev.pointerId === pendingId) {
      if (holdRipple) {
        const box = overlay.getBoundingClientRect();
        holdRipple.style.left = `${ev.clientX - box.left}px`;
        holdRipple.style.top = `${ev.clientY - box.top}px`;
      }
      if (held && !withinSlop(downAt.x, downAt.y, ev.clientX, ev.clientY)) startDrag();
      return;
    }
    if (ev.pointerId !== dragId) return;
    pendingMove = { ...point(ev.clientX, ev.clientY), button: dragButton };
    if (!raf) raf = requestAnimationFrame(flush);
  };
  const onUp = (ev: PointerEvent) => {
    pts.delete(ev.pointerId);
    if (ev.pointerId === dragId) {
      endDrag(ev.clientX, ev.clientY);
    } else if (ev.pointerId === pendingId) {
      clearHold();
      if (!ignoreUntilClear) tapAtDown();
    }
    if (pts.size < 2) pinch = null;
    if (pts.size === 0) ignoreUntilClear = false;
  };
  const onKeyDown = (ev: KeyboardEvent) => {
    ev.preventDefault();
    if (ev.repeat) return;
    send({ type: "keydown", key: ev.key });
  };
  const onKeyUp = (ev: KeyboardEvent) => {
    ev.preventDefault();
    send({ type: "keyup", key: ev.key });
  };
  const onContext = (ev: Event) => ev.preventDefault();
  video.addEventListener("wheel", onWheel, { passive: false });
  video.addEventListener("pointerdown", onDown);
  video.addEventListener("pointerup", onUp);
  video.addEventListener("pointercancel", onUp);
  video.addEventListener("pointermove", onMove);
  video.addEventListener("keydown", onKeyDown);
  video.addEventListener("keyup", onKeyUp);
  video.addEventListener("contextmenu", onContext);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    clearHold();
    video.removeEventListener("wheel", onWheel);
    video.removeEventListener("pointerdown", onDown);
    video.removeEventListener("pointerup", onUp);
    video.removeEventListener("pointercancel", onUp);
    video.removeEventListener("pointermove", onMove);
    video.removeEventListener("keydown", onKeyDown);
    video.removeEventListener("keyup", onKeyUp);
    video.removeEventListener("contextmenu", onContext);
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

async function sampleStats(pc: RTCPeerConnection, cursor: StatsCursor): Promise<Stats | null> {
  const report = await pc.getStats();
  let inbound: Record<string, unknown> | null = null;
  const codecs = new Map<string, string>();
  let rtt = 0;
  report.forEach((row) => {
    const r = row as Record<string, unknown>;
    if (r.type === "inbound-rtp" && r.kind === "video") inbound = r;
    else if (r.type === "codec") codecs.set(String(r.id), String(r.mimeType || ""));
    else if (r.type === "candidate-pair" && (r.nominated || r.state === "succeeded")) {
      const v = Number(r.currentRoundTripTime || 0);
      if (v > 0) rtt = v;
    }
  });
  if (!inbound) return null;
  const row = inbound as Record<string, unknown>;
  const bytes = Number(row.bytesReceived || 0);
  const now = performance.now();
  const dt = Math.max(1, now - cursor.at) / 1000;
  const kbps = cursor.at ? ((bytes - cursor.bytes) * 8) / dt / 1000 : 0;
  cursor.bytes = bytes;
  cursor.at = now;
  const mime = codecs.get(String(row.codecId || "")) || "";
  return {
    codec: mime.split("/")[1] || "",
    w: Number(row.frameWidth || 0),
    h: Number(row.frameHeight || 0),
    fps: Math.round(Number(row.framesPerSecond || 0)),
    kbps: Math.round(kbps),
    rtt: Math.round(rtt * 1000),
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
        onUp?.();
      }}
      onPointerCancel={(ev) => {
        ev.preventDefault();
        ev.stopPropagation();
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
    send({ type: "up", x: 0.5, y: 0.5, button: 2 });
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
      send({ type: "move", x: 0.5, y: 0.5 });
      send({ type: "down", x: 0.5, y: 0.5, button: 2 });
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
      )}
    </div>
  );
}

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [reason, setReason] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [attempt, setAttempt] = useState(0);
  const attemptRef = useRef(0);
  const controls = useRemoteViewControls();
  const rows = useWindowViews(!!hwnd);
  const kind = rows.find((r) => r.id === hwnd)?.kind;
  const kindRef = useRef(kind);
  if (kind) kindRef.current = kind;
  const watchingUefn = (kind || kindRef.current) === "uefn";
  const viewing = useViewerActive();

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

  useEffect(() => {
    attemptRef.current = 0;
    setAttempt(0);
    resetRemoteViewPanZoom();
  }, [hwnd]);

  useEffect(() => {
    if (!hwnd || !viewing) return;
    let closed = false;
    let live = false;
    let retryTimer = 0;
    setPhase("connecting");
    setReason("");
    setStats(null);

    const fail = (why: string, kind: Phase = "failed") => {
      if (closed) return;
      closed = true;
      setPhase(kind);
      setReason(why);
      window.clearTimeout(connectTimer);
      // A few automatic retries, then stop: a viewer that retries forever makes
      // the desktop re-capture and re-raise the window on every attempt.
      if (kind === "failed" && attemptRef.current < MAX_AUTO_RETRIES) {
        const delay = RETRY_MS[Math.min(attemptRef.current, RETRY_MS.length - 1)];
        retryTimer = window.setTimeout(retry, delay);
      }
    };

    // Direct (tunnel-free) mode: the transport already owns the peer
    // connection; ask the desktop to attach the cropped window track.
    const direct = getDirectTransport();
    if (direct) {
      const connectTimer = 0;
      void connectTimer;
      const video = videoRef.current;
      const unsubVideo = direct.onVideo((stream) => {
        if (!video || closed) return;
        video.srcObject = stream;
        if (stream) void video.play().catch(() => {});
      });
      const unsubStatus = direct.onStatus((st) => {
        if (closed) return;
        if (st.state === "failed") fail(st.reason || "Desktop connection lost.");
      });
      void direct.invoke("watch_window", [{ hwnd }]).then(
        () => {
          if (closed) return;
          live = true;
          setPhase("live");
          setReason("");
          for (const r of direct.peer?.getReceivers() ?? []) zeroPlayoutDelay(r);
          const size = overlaySize(overlayRef.current);
          if (size) direct.sendInput({ type: "size", ...size });
        },
        (err: unknown) => fail(`Desktop could not start screen capture: ${String((err as Error)?.message || err)}`),
      );
      const cursor: StatsCursor = { bytes: 0, at: 0 };
      const statsTimer = window.setInterval(() => {
        const pc = direct.peer;
        if (!pc || pc.connectionState !== "connected") return;
        void sampleStats(pc, cursor).then((st) => {
          if (st && !closed) setStats(st);
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
    const connectTimer = window.setTimeout(
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
        live = true;
        window.clearTimeout(connectTimer);
        setPhase("live");
        setReason("");
        return;
      }
      if (st === "failed") fail(live ? "Peer connection dropped." : "Peer connection failed (network may block UDP).");
    };
    ws.onopen = () => {
      const size = overlaySize(overlayRef.current);
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
    const statsTimer = window.setInterval(() => {
      if (pc.connectionState !== "connected") return;
      void sampleStats(pc, cursor).then((s) => {
        if (s && !closed) setStats(s);
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
    const el = overlayRef.current;
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
    return attachInput(el, send, view, overlay);
  }, [hwnd, phase, attempt, send]);

  useEffect(() => {
    bindRemoteSend(phase === "live" ? send : null);
    return () => bindRemoteSend(null);
  }, [phase, send]);

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      <div className="remote-window-view" ref={viewRef}>
        <div
          className="remote-window-stage"
          ref={(el) => {
            bindViewStage(el);
          }}
        >
          <video
            ref={videoRef}
            className={phase === "live" ? "remote-window-canvas" : "remote-window-canvas is-offscreen"}
            autoPlay
            muted
            playsInline
            disablePictureInPicture
            tabIndex={0}
          />
        </div>
      </div>
      {phase !== "live" ? (
        <div className="remote-window-status">
          <p className="remote-window-overlay-msg">
            {phase === "connecting" ? "Connecting to desktop…" : reason}
          </p>
          {phase === "kicked" ? (
            <button type="button" className="remote-window-retry" onClick={retry}>
              Take over
            </button>
          ) : phase === "failed" ? (
            <button type="button" className="remote-window-retry" onClick={retry}>
              Retry now
            </button>
          ) : null}
        </div>
      ) : null}
      {phase === "live" && stats ? (
        <span className="remote-window-stats">{statsLabel(stats)}</span>
      ) : null}
      {phase === "live" && controls.overlay && watchingUefn ? (
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
  );
}
