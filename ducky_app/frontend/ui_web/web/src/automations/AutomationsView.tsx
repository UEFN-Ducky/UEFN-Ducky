import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useWorkflowHistory, editableWorkflow } from "./useWorkflowHistory";
import { AutomationTemplatePicker } from "./AutomationTemplatePicker";
import { readDetailsTab, WorkflowInspector, writeDetailsTab, type DetailsTab, type InspectorTab } from "./WorkflowInspector";
import { blankCodeConfig, CODE_TYPE, dropWires, isCodeNode, wiresDropped } from "./codeNode";
import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { CanvasMenu } from "./CanvasMenu";
import type {
  AutomationDto,
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphNodeDto,
  AutomationNodeDto,
  AutomationRunDto,
  AutomationRunStepDto,
  AutomationSummaryDto,
  AutomationTemplateDto,
  FileRefDto,
  PanelPushEvent,
  PinDto,
  PinType,
  WorkflowOwnerDto,
  WorkflowOwnersDto,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { onApiReady } from "../hooks/onApiReady";
import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { WorkflowOutline } from "./WorkflowOutline";
import { LOCAL_OWNER, WorkflowList, ownerHelp, ownerName } from "./WorkflowList";
import { PanelResizeHandle } from "./PanelResizeHandle";
import { takePendingGraphFocusTarget, type GraphFocus } from "../hooks/graphActivity";
import { getTargetElement, registerTarget, targetRef, unregisterTarget } from "../ui-targets/registry";
import { cssEscape, registerTargetResolver, registerUiAction } from "../ui-targets/resolve";
import { requestOpenWorkflowsTab } from "../navigation/openWorkflowsTab";
import { runAgentWalkthrough } from "../walkthrough/agentWalkthrough";
import { redoTour } from "../walkthrough/WalkthroughService";
import { buildWorkflowTour, setCurrentWorkflow, withOpenStep } from "./workflowTour";
import { startFirstOpenTour } from "../walkthrough/firstOpen";
import { WORKFLOWS_EDITOR_TOUR_ID, WORKFLOWS_FIRST_BUILD_TOUR_ID, WORKFLOWS_INTRO_TOUR_ID } from "./workflowsTours";
import { Icons } from "../icons/Icons";
import { copyText } from "../utils/copyText";
import { formatRunLog, runLogHasContent } from "./runLog";
import { TerminalOutput, type TerminalSnapshot } from "./TerminalOutput";
import type { LiveNodeRun } from "./LiveNodeStatus";

import { basedOnNode, GroupIcon, isEndNode, nodeLabel, nodeRole, nodeSummary, NodeIcon, useNodeFaces } from "./NodeVisuals";
import { cleanGroups, deleteNodes, groupBounds, groupDepth, groupLocked, groupMembers, groupNodes, intersects, nodeLocked, removeGroup, selectionRect, ungroupNodes, type GraphRect } from "./workflowGroups";
import { buildFolderTree, folderName, folderPaths, isInside, loadEmptyFolders, movedPath, normalizeFolder, parentFolder, saveEmptyFolders } from "./workflowFolders";
import { describeSignature } from "./FunctionSettings";
import { planGroupExtraction } from "./extractGroup";
import { accepts, cleanType, firstFit, nodeLayout, nodePins, pinPoint, shortValue, type NodeLayout, type NodePins } from "./pins";
import { clampZoom, fitCamera, gridScale, groupTitleScale, portPoint, wirePath, zoomAt, NODE_HEIGHT, NODE_HEIGHT_COMPACT, NODE_WIDTH, OVERVIEW_ZOOM } from "./graphGeometry";
const LOG_H_MIN = 140;
const LOG_H_MAX = 560;
const LOG_H_DEFAULT = 220;
/** What an empty list offers to start from (shown only when their plugins are set up). */
const FEATURED_TEMPLATES = ["builtin:pipe-prompt-image", "builtin:pipe-prompt-3d-uefn", "builtin:pipe-character-full", "builtin:playtest-start", "builtin:pipe-title-card"];

const GROUP_ORDER = ["Starting", "Triggers", "Inputs", "Functions", "Agents", "Duckies", "Text & AI", "Images", "Image tools", "3D", "3D tools", "Characters",
  "Blender", "UEFN", "Play test", "Lists", "Documents", "Tools", "Logic", "Code", "Utility", "End"];
/** Canvas units a group's title takes above its box at 100%; nested boxes leave this room.
 *  Zoomed out the title is drawn bigger (groupTitleScale) so it stays readable. */
const GROUP_TITLE_PX = 44;
/** How long the camera glides to a fit, and how long an agent's change stays lit. */
const GLIDE_MS = 320;
const FLASH_MS = 2400;

/** Where you were (open workflow, folded list, each workflow's camera), kept on this PC so a
 *  hidden tab, a pop-out window or a restart comes back to the same place. */
const VIEW_KEY = "ducky.workflows.view.v1";
type Camera = { x: number; y: number; zoom: number };
type Tool = "select" | "hand";
type GridStyle = "squares" | "dots" | "none";
type SavedView = { open?: string; collapsed?: boolean; tool?: Tool; cameras?: Record<string, Camera>; snap?: boolean; grid?: GridStyle };

function readView(): SavedView {
  try {
    const saved = JSON.parse(window.localStorage.getItem(VIEW_KEY) || "{}");
    return saved && typeof saved === "object" ? saved as SavedView : {};
  } catch {
    return {};
  }
}

function writeView(patch: Partial<SavedView>) {
  try { window.localStorage.setItem(VIEW_KEY, JSON.stringify({ ...readView(), ...patch })); } catch { /* private mode */ }
}

const END_TYPES = new Set(["pipeline.finish", "flow.end", "flow.output"]);
/** Room under a card's pin rows: a Preview's value, an Input's picked picture, or the
 *  picture a node made with Try again / Use this under it. */
const PREVIEW_EXTRA = 120;
const THUMB_EXTRA = 120;
const MADE_EXTRA = 210;
const PICTURE = /\.(png|jpe?g|webp|gif|bmp|svg)$/i;

/** Empty folders that older builds kept in this browser only: hand them to the host once
 *  (a read-only team's wait until it can take them). */
async function keepOldEmptyFolders(owners: WorkflowOwnerDto[]): Promise<void> {
  const left = loadEmptyFolders();
  if (!getApi()?.add_workflow_folder || !Object.keys(left).length) return;
  const handed = Object.entries(left).filter(([id]) => owners.some((owner) => owner.id === id && !owner.readOnly));
  if (!handed.length) return;
  saveEmptyFolders(Object.fromEntries(Object.entries(left).filter(([id]) => !handed.some(([done]) => done === id))));
  for (const [id, paths] of handed) for (const path of paths) await getApi()?.add_workflow_folder?.(id, path);
}

/** A node that makes pictures (Text to Image, Edit image, a render…), not an Input. */
function makesPictures(node: AutomationGraphNodeDto, pins: NodePins): boolean {
  return !node.type.startsWith("input.") && node.type !== "util.preview"
    && pins.outputs.some((pin) => pin.type === "image" || pin.type === "images");
}

function cardExtra(node: AutomationGraphNodeDto, pins: NodePins): number {
  if (node.type === "util.preview") return PREVIEW_EXTRA;
  if (makesPictures(node, pins)) return MADE_EXTRA;
  return pins.outputs.some((pin) => pin.type === "image" || pin.type === "images") ? THUMB_EXTRA : 0;
}

/** What each node made, the newest run winning: a card keeps its last picture after a
 *  later run of it fails. */
function madeByNode(runs: (AutomationRunDto | null | undefined)[]): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const run of runs) {
    for (const [id, values] of Object.entries(run?.node_outputs || {})) {
      if (values && typeof values === "object") out[id] = values as Record<string, unknown>;
    }
  }
  return out;
}

/** The pictures in a value (a file ref, a list of them), at most four. */
function picturesIn(value: unknown): FileRefDto[] {
  const list = Array.isArray(value) ? value : value ? [value] : [];
  return list.filter((item): item is FileRefDto => !!item && typeof item === "object" && "path" in (item as object)
    && ((item as FileRefDto).kind === "image" || PICTURE.test(String((item as FileRefDto).path)))).slice(0, 4);
}

/** Pictures a card shows: what its image outputs made last run (an Input its pick). */
function cardPictures(node: AutomationGraphNodeDto, pins: NodePins, shown: Record<string, unknown>): FileRefDto[] {
  for (const pin of pins.outputs) {
    if (pin.type !== "image" && pin.type !== "images") continue;
    const found = picturesIn(shown[pin.id] ?? (node.type.startsWith("input.") ? node.config.value : undefined));
    if (found.length) return found;
  }
  return [];
}

function Thumbs({ pictures, empty }: { pictures: FileRefDto[]; empty: string }) {
  if (!pictures.length) return <div className="aw-node-thumb is-empty">{empty}</div>;
  return <div className={`aw-node-thumb is-${Math.min(pictures.length, 4)}`}>
    {pictures.map((pic, index) => pic.url
      ? <img key={`${pic.path}-${index}`} src={pic.url} alt={pic.name} title={pic.name} draggable={false} loading="lazy" />
      : <span key={`${pic.path}-${index}`} className="aw-node-thumb-name" title={pic.path}>{pic.name}</span>)}
  </div>;
}
/** Under a picture on a card: make another from the same prompt, or keep this one (run
 *  the steps that take it, like Save to card). */
function PictureActions({ nodeId, running, busy, onRun }: { nodeId: string; running: string; busy: boolean; onRun: (id: string, how?: "run" | "keep") => void }) {
  return <div className="aw-preview-actions" onPointerDown={(event) => event.stopPropagation()}>
    <button type="button" disabled={!!running || busy} title="Make a new one from the same prompt" onClick={() => onRun(nodeId)}>
      {running === nodeId ? <Icons.Spinner /> : <Icons.Refresh />} Try again
    </button>
    <button type="button" className="is-primary" disabled={!!running || busy} title="Keep this one: run the steps that take it (like Save to card)" onClick={() => onRun(nodeId, "keep")}>
      <Icons.Check /> Use this
    </button>
  </div>;
}
/** The add-node menu: at the pointer (it grows out of it) or above the + button (it slides up). */
/** A wire being drawn: white (exec) when it has no pin, else a data wire from that pin. */
type WireStart = { sourceId: string; dir: "in" | "out"; pin?: string; pinType?: PinType };
type SpawnAt = { x: number; y?: number; bottom?: number; origin?: string; worldX: number; worldY: number; wire?: WireStart };

function spawnAtPoint(clientX: number, clientY: number, world: { x: number; y: number }, wire?: SpawnAt["wire"]): SpawnAt {
  const x = Math.max(8, Math.min(clientX, window.innerWidth - 328));
  const y = Math.max(8, Math.min(clientY, window.innerHeight - 520));
  return { x, y, worldX: world.x, worldY: world.y, wire, origin: `${clientX - x}px ${clientY - y}px` };
}

const ZOOM_STEPS = [0.1, 0.25, 0.5, 1, 2];
const GRID_STYLES: { value: GridStyle; label: string }[] = [{ value: "squares", label: "Squares" }, { value: "dots", label: "Dots" }, { value: "none", label: "Nothing" }];

/** Ctrl + scroll over a panel (list, top bar, details, bottom toolbar, run log) zooms that
 *  panel only, like the Duckies panels; each keeps its own size on this PC. */
const PANEL_ZOOM_KEY = "ducky.workflows.panelZoom.v1";
const PANELS = ["list", "toolbar", "details", "controls", "log"] as const;
type PanelName = (typeof PANELS)[number];
type PanelZoom = Record<PanelName, number>;
const clampPanelZoom = (value: number) => Math.round(Math.min(2, Math.max(0.5, value)) * 100) / 100;

function loadPanelZoom(): PanelZoom {
  let saved: Partial<PanelZoom> = {};
  try { saved = JSON.parse(window.localStorage.getItem(PANEL_ZOOM_KEY) || "{}") || {}; } catch { /* private mode */ }
  return Object.fromEntries(PANELS.map((name) => [name, typeof saved[name] === "number" && Number.isFinite(saved[name]) ? clampPanelZoom(saved[name]!) : 1])) as PanelZoom;
}
/** Press and hold the canvas (mouse or finger) to open the add-node menu there. */
const HOLD_MS = 550;

/** Side panel widths (drag their inner edge), kept on this PC. */
const PANEL_WIDTHS_KEY = "ducky.workflows.panelWidths.v1";
const LIST_W = { min: 180, max: 480, initial: 220 };
/** Editor width at or below which it uses the phone layout (matches @container 680px in the CSS). */
const PHONE_EDITOR_W = 680;
const INSPECTOR_W = { min: 280, max: 640, initial: 340 };
/** The details panel's width while Pop out is on in a node's Code tab (the CSS caps it to the editor). */
const CODE_WIDE_W = 760;
type PanelWidths = { list: number; inspector: number };

function fitWidth(value: unknown, limits: { min: number; max: number; initial: number }): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(limits.max, Math.max(limits.min, Math.round(value))) : limits.initial;
}

function loadPanelWidths(): PanelWidths {
  try {
    const saved = JSON.parse(window.localStorage.getItem(PANEL_WIDTHS_KEY) || "{}") as Partial<PanelWidths>;
    return { list: fitWidth(saved.list, LIST_W), inspector: fitWidth(saved.inspector, INSPECTOR_W) };
  } catch {
    return { list: LIST_W.initial, inspector: INSPECTOR_W.initial };
  }
}

/** Unreal's exec pin: hollow until a wire is attached. */
const EXEC_PIN = <svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2 H8 L12.5 7 L8 12 H2 Z" /></svg>;

/** A palette tile; function tiles add a Run workflow node already pointed at a workflow. */
type PaletteTile = AutomationNodeDto & { workflowId?: string };

export function clampLogHeight(h: number, boardH = 0): number {
  const cap = boardH > 0 ? Math.max(LOG_H_MIN, boardH - 24) : LOG_H_MAX;
  return Math.min(LOG_H_MAX, cap, Math.max(LOG_H_MIN, h));
}

function emptyGraph(): AutomationGraphDto {
  return { nodes: [], edges: [] };
}

function nid(): string {
  return `n${Math.random().toString(36).slice(2, 10)}`;
}

/** Search: every word typed must appear in the node's name, description, group or type. */
function groupCatalog(catalog: PaletteTile[], query: string) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const map = new Map<string, PaletteTile[]>();
  for (const n of catalog) {
    const text = `${n.label} ${n.description || ""} ${n.group} ${n.type}`.toLowerCase();
    if (!words.every((word) => text.includes(word))) continue;
    const g = n.group || "Nodes";
    const list = map.get(g) || [];
    list.push(n);
    map.set(g, list);
  }
  const keys = [...map.keys()].sort((a, b) => {
    const ia = GROUP_ORDER.indexOf(a);
    const ib = GROUP_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return keys.map((k) => [k, map.get(k) || []] as const);
}

/** Team workflows sync once when the view opens and then only after a save: saves
 *  close together go up as one round, a few seconds after the last. */
const TEAM_SYNC_DELAY_MS = 4000;
/** A test run may take long (UEFN, duckies); Stop ends it any time. */
const RUN_TIMEOUT_MS = 6 * 60 * 60 * 1000;
/** The server turned a save down and said why (too much code, say): retrying won't help. */
class SaveRefused extends Error {}

/** A run as it happens: the step running now, the wire it came along, how each step went. */
type LiveState = "running" | "ok" | "error" | "stopped";
export type LiveRun = {
  outputs: Record<string, TerminalSnapshot>;
  /** What a Custom code step wrote with ducky.log, kept apart from its terminal output. */
  logs: Record<string, string>;
  /** When each step started and ended (ms), for its details. */
  times: Record<string, { started: number; ended?: number }>;
  run: string;
  active: string;
  from: string;
  states: Record<string, LiveState>;
  passed: string[];
  finished: "" | "done" | "error" | "stopped";
  lines: { node: string; label: string; state: LiveState; error?: string }[];
};

export function liveRunReducer(current: LiveRun | null, event: PanelPushEvent): LiveRun | null {
  const run = String(event.run || "");
  const fresh: LiveRun = { run, active: "", from: "", states: {}, passed: [], finished: "", lines: [], outputs: {}, logs: {}, times: {} };
  const base = current && current.run === run ? current : fresh;
  // Output and steps belong to a run seen starting; others (a Code-tab Test, an older run) are not this one.
  if ((event.type === "workflow_output" || event.type === "workflow_step") && (!current || current.run !== run)) return current;
  if (event.type === "workflow_output") {
    if (!event.node) return current;
    if (event.source === "log") return { ...base, logs: { ...base.logs, [event.node]: event.output || "" } };
    return { ...base, outputs: { ...base.outputs, [event.node]: { output: event.output || "", sessionId: event.session_id || "" } } };
  }
  if (event.type === "workflow_run") {
    if (event.state === "started") return fresh;
    const finished = event.state === "stopped" ? "stopped" : event.state === "error" ? "error" : "done";
    return { ...base, active: "", from: "", finished };
  }
  const node = String(event.node || "");
  if (!node) return base;
  const state = (["running", "ok", "error", "stopped"].includes(String(event.state)) ? event.state : "running") as LiveState;
  if (state === "running") {
    const wire = event.from ? `${event.from}>${node}` : "";
    return {
      ...base, active: node, from: event.from || "", states: { ...base.states, [node]: "running" }, times: { ...base.times, [node]: { started: Date.now() } },
      passed: wire && !base.passed.includes(wire) ? [...base.passed, wire] : base.passed,
      lines: [...base.lines, { node, label: event.label || node, state }],
    };
  }
  const lines = [...base.lines];
  const at = lines.map((line) => line.node).lastIndexOf(node);
  if (at >= 0) lines[at] = { ...lines[at], state, ...(event.error ? { error: event.error } : {}) };
  const times = { ...base.times, [node]: { started: base.times[node]?.started ?? Date.now(), ended: Date.now() } };
  return { ...base, active: base.active === node ? "" : base.active, states: { ...base.states, [node]: state }, lines, times };
}

/** A Preview node's content: text as is, data as JSON, a file by name. */
function previewText(value: unknown): string {
  if (value === undefined) return "Run the workflow to see what arrives here.";
  if (value === null || value === "") return "(nothing)";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return shortValue(value);
  if (value && typeof value === "object" && "path" in (value as object)) return `${(value as { name?: string }).name || "file"}`;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

/** `onOpenCode`: a failed custom code step opens its code at the line that failed. */
function RunSteps({ steps, onOpenCode }: { steps: AutomationRunStepDto[]; onOpenCode?: (step: AutomationRunStepDto) => void }) {
  return <>{steps.map((s, i) => {
    const text = `${s.label || s.type} ${s.ok === false ? `— ${s.error}` : s.stop ? "ok · no Return reached, this path stopped" : "ok"}`;
    const code = !!onOpenCode && !!s.id && s.ok === false && (!!s.code_error || s.type === CODE_TYPE);
    return <li key={i} className={s.ok === false ? "is-err" : ""} data-aw-log-node={s.id || undefined}>
      {code ? <button type="button" className="aw-log-step-link" title="Open its code at the line that failed" onClick={() => onOpenCode?.(s)}>{text}</button> : text}
      {s.substeps?.length ? <ol className="aw-log-substeps"><RunSteps steps={s.substeps} /></ol> : null}
    </li>;
  })}</>;
}

export function AutomationsView() {
  const sectionId = useId();
  const { confirm } = useConfirmModal();
  const [listCollapsed, setListCollapsed] = useState(() => !!readView().collapsed);
  useEffect(() => writeView({ collapsed: listCollapsed }), [listCollapsed]);
  // Select drags out a selection box, Hand moves around; both work on nodes and wires.
  // Holding Space is Hand for as long as it is held.
  const [tool, setTool] = useState<Tool>(() => readView().tool === "select" ? "select" : "hand");
  useEffect(() => writeView({ tool }), [tool]);
  // Dragged and new nodes land on the grid unless snapping is off (bottom toolbar).
  const [snap, setSnap] = useState(() => readView().snap !== false);
  useEffect(() => writeView({ snap }), [snap]);
  const [gridStyle, setGridStyle] = useState<GridStyle>(() => GRID_STYLES.some((item) => item.value === readView().grid) ? readView().grid! : "squares");
  useEffect(() => writeView({ grid: gridStyle }), [gridStyle]);
  const rootRef = useRef<HTMLDivElement>(null);
  const [panelZoom, setPanelZoom] = useState(loadPanelZoom);
  useEffect(() => { try { window.localStorage.setItem(PANEL_ZOOM_KEY, JSON.stringify(panelZoom)); } catch { /* private mode */ } }, [panelZoom]);
  const [panelBadge, setPanelBadge] = useState<{ value: number; left: number; top: number } | null>(null);
  const badgeTimer = useRef(0);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      const panel = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-aw-zoom]") : null;
      const name = panel?.dataset.awZoom as PanelName | undefined;
      if (!panel || !name || !PANELS.includes(name)) return;
      event.preventDefault();
      event.stopPropagation();
      setPanelZoom((current) => {
        const value = clampPanelZoom(current[name] - event.deltaY * 0.001);
        const rect = panel.getBoundingClientRect();
        setPanelBadge({ value, left: rect.right - 10, top: rect.top + 10 });
        return value === current[name] ? current : { ...current, [name]: value };
      });
      window.clearTimeout(badgeTimer.current);
      badgeTimer.current = window.setTimeout(() => setPanelBadge(null), 1200);
    };
    root.addEventListener("wheel", onWheel, { passive: false, capture: true });
    return () => { root.removeEventListener("wheel", onWheel, { capture: true }); window.clearTimeout(badgeTimer.current); };
  }, []);
  const [spaceHand, setSpaceHand] = useState(false);
  const activeTool: Tool = spaceHand ? "hand" : tool;
  useEffect(() => {
    if (!spaceHand) return;
    const up = (event: KeyboardEvent) => { if (event.key === " ") setSpaceHand(false); };
    const blur = () => setSpaceHand(false);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keyup", up); window.removeEventListener("blur", blur); };
  }, [spaceHand]);
  // A short note at the top (e.g. "Pause is locked"), gone after a few seconds.
  const [notice, setNotice] = useState<{ id: number; text: string; kind: "lock" | "note" } | null>(null);
  const noticeTimer = useRef(0);
  const notify = (text: string, kind: "lock" | "note" = "lock") => {
    setNotice((current) => ({ id: (current?.id || 0) + 1, text, kind }));
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), kind === "note" ? 5000 : 2800);
  };
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  const [panelWidths, setPanelWidths] = useState(loadPanelWidths);
  const [resizingPanel, setResizingPanel] = useState(false);
  useEffect(() => {
    if (resizingPanel) return;  // saved once the drag ends
    try { window.localStorage.setItem(PANEL_WIDTHS_KEY, JSON.stringify(panelWidths)); } catch { /* private mode */ }
  }, [panelWidths, resizingPanel]);
  // Grid, snap, tool, panel sizes and zoom, folded list: also kept on disk (AppData), so a
  // WebView storage wipe or another window doesn't lose them. Saved only after they are read.
  const prefsRead = useRef(false);
  useEffect(() => onApiReady(() => void (async () => {
    try {
      const saved = (await getApi()?.workflow_editor_prefs?.())?.prefs || {};
      if (saved.grid === "squares" || saved.grid === "dots" || saved.grid === "none") setGridStyle(saved.grid);
      if (typeof saved.snap === "boolean") setSnap(saved.snap);
      if (saved.tool === "select" || saved.tool === "hand") setTool(saved.tool);
      if (typeof saved.collapsed === "boolean") setListCollapsed(saved.collapsed);
      if (saved.panelWidths && typeof saved.panelWidths === "object") {
        const widths = saved.panelWidths as Partial<PanelWidths>;
        setPanelWidths({ list: fitWidth(widths.list, LIST_W), inspector: fitWidth(widths.inspector, INSPECTOR_W) });
      }
      if (saved.panelZoom && typeof saved.panelZoom === "object") {
        const zooms = saved.panelZoom as Partial<PanelZoom>;
        setPanelZoom((current) => Object.fromEntries(PANELS.map((name) => [name, typeof zooms[name] === "number" ? clampPanelZoom(zooms[name]!) : current[name]])) as PanelZoom);
      }
    } finally {
      prefsRead.current = true;
    }
  })()), []);
  useEffect(() => {
    if (!prefsRead.current || resizingPanel) return;
    const timer = window.setTimeout(() => void getApi()?.set_workflow_editor_prefs?.({ grid: gridStyle, snap, tool, collapsed: listCollapsed, panelWidths, panelZoom }), 400);
    return () => window.clearTimeout(timer);
  }, [gridStyle, snap, tool, listCollapsed, panelWidths, panelZoom, resizingPanel]);
  const [rows, setRows] = useState<AutomationSummaryDto[]>([]);
  const [owners, setOwners] = useState<WorkflowOwnersDto>({ owners: [LOCAL_OWNER] });
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [pickerOwner, setPickerOwner] = useState(LOCAL_OWNER.id);
  const [pickerFolder, setPickerFolder] = useState("");
  // Folders the host keeps per owner (empty ones too; a team's sync), plus ones made or
  // left here that the next list refresh has not brought back yet.
  const [emptyFolders, setEmptyFolders] = useState<Record<string, string[]>>({});
  const folderLists = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const owner of owners.owners || []) out[owner.id] = [...(owner.folders || [])];
    for (const [id, paths] of Object.entries(emptyFolders)) out[id] = [...new Set([...(out[id] || []), ...paths])];
    return out;
  }, [owners, emptyFolders]);
  const [actionError, setActionError] = useState("");
  const listRef = useRef<HTMLElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [catalog, setCatalog] = useState<AutomationNodeDto[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const history = useWorkflowHistory();
  const { draft, setDraft, replace: acknowledgeDraft, reset: resetDraft, begin: beginEdit, end: endEdit } = history;
  const [versions, setVersions] = useState<{ id: string; name: string; saved_at: number; node_count: number; note?: string }[]>([]);
  const [historyStatus, setHistoryStatus] = useState("");
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [marquee, setMarquee] = useState<GraphRect | null>(null);
  const marqueeRef = useRef<{ pointerId: number; start: { x: number; y: number }; base: string[] } | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<number | null>(null);
  /** The details panel tab last picked or clicked on the canvas ("node:<id>", "group:<id>", "edge:<i>"). */
  const [inspectorKey, setInspectorKey] = useState("");
  // A node's details show Settings or Code (kept on this PC); Pop out widens them for code.
  const [detailsTab, setDetailsTab] = useState<DetailsTab>(readDetailsTab);
  const pickDetailsTab = (tab: DetailsTab) => { setDetailsTab(tab); writeDetailsTab(tab); };
  const [codeWide, setCodeWide] = useState(false);
  const [codeFocus, setCodeFocus] = useState<{ nodeId: string; line: number; nonce: number } | null>(null);
  /** The custom code node being typed in: its wires to pins the code drops stay until typing ends. */
  const codeSession = useRef("");
  const [groupsCollapsed, setGroupsCollapsed] = useState(false);
  const [pan, setPan] = useState({ x: 280, y: 160 });
  const [zoom, setZoom] = useState(1);
  // Fit and agent focus glide the camera there instead of jumping.
  const [gliding, setGliding] = useState(false);
  const glideTimer = useRef(0);
  const glideTo = (camera: { x: number; y: number; zoom: number }) => {
    window.clearTimeout(glideTimer.current);
    setGliding(true);
    setZoom(camera.zoom);
    setPan({ x: camera.x, y: camera.y });
    glideTimer.current = window.setTimeout(() => setGliding(false), GLIDE_MS);
  };
  const stopGlide = () => { if (gliding) { window.clearTimeout(glideTimer.current); setGliding(false); } };
  // Nodes an agent just changed or is pointing at light up for a moment.
  const [flashIds, setFlashIds] = useState<string[]>([]);
  const flashTimer = useRef(0);
  const flash = (ids: string[]) => {
    window.clearTimeout(flashTimer.current);
    setFlashIds(ids);
    flashTimer.current = window.setTimeout(() => setFlashIds([]), FLASH_MS);
  };
  useEffect(() => () => { window.clearTimeout(glideTimer.current); window.clearTimeout(flashTimer.current); }, []);
  /** Where an agent asked to look, once that workflow has loaded. */
  const [pendingShow, setPendingShow] = useState<GraphFocus | null>(null);
  const [wireFrom, setWireFrom] = useState<string | null>(null);
  const [draftWire, setDraftWire] = useState<{
    sourceId: string;
    dir: "in" | "out";
    pinType?: PinType;
    fromX: number;
    fromY: number;
    toX: number;
    toY: number;
  } | null>(null);
  const [log, setLog] = useState<AutomationRunDto | null>(null);
  const [made, setMade] = useState<Record<string, Record<string, unknown>>>({});
  useEffect(() => { if (log?.node_outputs) setMade((before) => ({ ...before, ...madeByNode([log]) })); }, [log]);
  // Phones (narrow editor): the toolbar actions and the canvas tools each fold into one
  // button that opens them as a labeled list. Wide, both are always shown (CSS).
  const [phoneMenu, setPhoneMenu] = useState<"" | "actions" | "canvas">("");
  useEffect(() => {
    if (!phoneMenu) return;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      // Inside the editor but outside the open list: close. Outside the editor (a menu
      // the list opened, portaled to the body) keeps it open.
      if (!target || !rootRef.current?.contains(target)) return;
      if (target.closest(".aw-toolbar-actions, .aw-more-toggle, .aw-canvas-controls, .aw-canvas-fab")) return;
      setPhoneMenu("");
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setPhoneMenu(""); };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown, true); document.removeEventListener("keydown", onKey); };
  }, [phoneMenu]);
  /** A pick in an open phone list (a plain action, or an item of a menu it opened) closes it. */
  const closePhoneMenuAfterPick = (event: React.MouseEvent) => {
    const target = event.target as Element;
    if (target.closest("button:not([aria-haspopup]), input[type=radio], [role=option], [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox]")) setPhoneMenu("");
  };
  const [logOpen, setLogOpen] = useState(false);
  const [logCopied, setLogCopied] = useState(false);
  const [logHeight, setLogHeight] = useState(LOG_H_DEFAULT);
  const logResizeRef = useRef<{ startY: number; startH: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [spawn, setSpawn] = useState<SpawnAt | null>(null);
  const [spawnFilter, setSpawnFilter] = useState("");
  const spawnSearchRef = useRef<HTMLInputElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const boardRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ start: { x: number; y: number }; origins: { id: string; x: number; y: number }[]; clickIds: string[]; clickKey: string; moved: boolean; blocked: string; warned?: boolean; grid: number } | null>(null);
  const holdRef = useRef<{ timer: number; x: number; y: number; pointerId: number } | null>(null);
  const panRef = useRef<{ x: number; y: number; px: number; py: number; button: number; moved: boolean } | null>(null);
  const touches = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; center: { x: number; y: number }; pan: { x: number; y: number }; zoom: number } | null>(null);
  const suppressMenuUntil = useRef(0);
  const wireRef = useRef<(WireStart & { fromX: number; fromY: number }) | null>(null);

  const byType = useMemo(() => {
    const m = new Map<string, AutomationNodeDto>();
    for (const n of catalog) m.set(n.type, n);
    return m;
  }, [catalog]);

  const refreshList = useCallback(async () => {
    const api = getApi();
    const [listed, folders] = await Promise.all([api?.list_workflows?.(), api?.workflow_owners?.()]);
    setRows(listed?.workflows || []);
    if (folders?.owners?.length) {
      setOwners(folders);
      if (folders.owners.some((owner) => Array.isArray(owner.folders))) setEmptyFolders({});
      void keepOldEmptyFolders(folders.owners);
    }
    setNowMs(Date.now());
  }, []);

  // A pop-out window has no bridge for its first moment: load once it answers.
  useEffect(() => onApiReady(() => { void refreshList(); }), [refreshList]);

  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "failed">("loading");
  const catalogTry = useRef(0);
  const loadCatalog = useCallback(async () => {
    const attempt = ++catalogTry.current;
    setCatalogState("loading");
    let nodes: AutomationNodeDto[] = [];
    try { nodes = (await getApi()?.list_workflow_nodes?.())?.nodes || []; } catch { /* tried again below */ }
    if (attempt !== catalogTry.current) return false;
    setCatalog(nodes);
    setCatalogState(nodes.length ? "ready" : "failed");
    return nodes.length > 0;
  }, []);
  // At startup or in a new window the bridge may not answer yet: try again a few times.
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const attempt = async (tries: number) => {
      const ok = await loadCatalog();
      if (!ok && !stopped && tries < 5) timer = window.setTimeout(() => void attempt(tries + 1), 1000 * 2 ** tries);
    };
    const stopWaiting = onApiReady(() => void attempt(0));
    return () => { stopWaiting(); stopped = true; window.clearTimeout(timer); catalogTry.current++; };
  }, [loadCatalog]);

  // Teams: ask the Store which teams this account has once per open and sync them once
  // (teammates' changes come in); after that only a save sends a round (queueTeamSync).
  // Another chat's edits and finished rounds arrive as graphs_changed.
  const hasTeams = !!owners.owners?.some((owner) => owner.kind === "team");
  const draftIdRef = useRef("");
  draftIdRef.current = draft?.id || "";
  const [live, setLive] = useState<LiveRun | null>(null);
  useEffect(() => {
    let cancelled = false;
    const stopWaiting = onApiReady(() => void (async () => {
      const folders = await getApi()?.workflow_owners?.(true);
      if (!cancelled && folders?.owners?.length) setOwners(folders);
    })());
    installPanelPushBus();
    const stop = subscribePanelPush((event) => {
      // A team round started elsewhere (a plugin's scope bar) can bring workflow changes too.
      const workflowsSynced = event.type === "plugin_scope_changed" && !!event.plugins?.includes("ducky.automations");
      if (event.type === "graphs_changed" || event.type === "duckyos_account_changed" || workflowsSynced) void refreshList();
      // The open workflow running (Play, a schedule, a chat): light up where it is.
      if ((event.type === "workflow_run" || event.type === "workflow_step" || event.type === "workflow_output") && event.id && event.id === draftIdRef.current) setLive((current) => liveRunReducer(current, event));
    });
    return () => { cancelled = true; stopWaiting(); stop(); };
  }, [refreshList]);
  useEffect(() => {
    if (!hasTeams) return;
    return onApiReady(() => void getApi()?.workflow_sync?.(false));
  }, [hasTeams]);
  const syncTimer = useRef(0);
  const queueTeamSync = () => {
    window.clearTimeout(syncTimer.current);
    syncTimer.current = window.setTimeout(() => { syncTimer.current = 0; void getApi()?.workflow_sync?.(true); }, TEAM_SYNC_DELAY_MS);
  };
  // Leaving with a save still queued: send it now.
  useEffect(() => () => { if (syncTimer.current) { window.clearTimeout(syncTimer.current); void getApi()?.workflow_sync?.(true); } }, []);
  // A finished run stays lit for a few seconds, then the canvas goes back to normal.
  useEffect(() => {
    if (!live?.finished) return;
    const timer = window.setTimeout(() => setLive((current) => current === live ? null : current), 8000);
    return () => window.clearTimeout(timer);
  }, [live]);
  // The details follow the step running now, unless something else was picked meanwhile.
  const selectionRef = useRef({ nodes: selectedNodeIds, edge: selectedEdge });
  selectionRef.current = { nodes: selectedNodeIds, edge: selectedEdge };
  const followedRef = useRef("");
  useEffect(() => {
    const active = live?.active || "";
    if (!active) return;
    const { nodes, edge } = selectionRef.current;
    if (edge === null && (!nodes.length || (nodes.length === 1 && nodes[0] === followedRef.current))) {
      setSelectedNodeIds([active]);
      setInspectorKey(`node:${active}`);
    }
    followedRef.current = active;
  }, [live?.active]);
  // Each step of the run going on now: its state, time, error and terminal log.
  const liveNodes = useMemo(() => {
    if (!live) return undefined;
    const nodes: Record<string, LiveNodeRun> = {};
    for (const [id, state] of Object.entries(live.states)) nodes[id] = { state, ...live.times[id] };
    for (const line of live.lines) if (line.error && nodes[line.node]) nodes[line.node]!.error = line.error;
    for (const [id, output] of Object.entries(live.outputs)) nodes[id] = { ...(nodes[id] || { state: "running" }), output };
    for (const [id, log] of Object.entries(live.logs)) nodes[id] = { ...(nodes[id] || { state: "running" }), log };
    return nodes;
  }, [live]);

  useEffect(() => {
    if (!spawn) return;
    spawnSearchRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSpawn(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [spawn]);

  const loadGen = useRef(0);
  const loadOne = useCallback(async (id: string) => {
    const gen = ++loadGen.current;
    const res = await getApi()?.get_workflow?.(id);
    if (gen !== loadGen.current) return;
    const row = res?.workflow;
    if (row) {
      resetDraft(row);
      synced.current = row;
      setVersions([]);
      setHistoryStatus("");
      setActionError("");
      setSelectedId(id);
      setSpawn(null);
      setSelectedNodeIds([]);
      setSelectedEdge(null);
      setLog((row.runs || []).slice(-1)[0] || null);
      setMade(madeByNode(row.runs || []));
      setLive(null);
      writeView({ open: id });
      const camera = readView().cameras?.[id];
      if (camera && [camera.x, camera.y, camera.zoom].every(Number.isFinite)) {
        setPan({ x: camera.x, y: camera.y });
        setZoom(clampZoom(camera.zoom));
      }
    }
  }, [resetDraft]);

  // Each workflow keeps its own camera.
  const cameraId = draft?.id || "";
  useEffect(() => {
    if (!cameraId) return;
    const timer = window.setTimeout(() => {
      const cameras = { ...(readView().cameras || {}) };
      delete cameras[cameraId];
      cameras[cameraId] = { x: Math.round(pan.x), y: Math.round(pan.y), zoom: Math.round(zoom * 1000) / 1000 };
      const ids = Object.keys(cameras);
      for (const old of ids.slice(0, Math.max(0, ids.length - 40))) delete cameras[old];
      writeView({ cameras });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [cameraId, pan, zoom]);

  useEffect(() => {
    // An agent saved, ran or is showing a workflow: open it, then bring what it touched into view.
    const open = (focus: GraphFocus) => {
      if (!focus.id) return;
      void refreshList();
      void loadOne(focus.id);
      if (focus.nodes?.length || focus.note) setPendingShow(focus);
      else setLogOpen(true);
    };
    const pending = takePendingGraphFocusTarget();
    let stopWaiting = () => {};
    if (pending) open(pending);
    else if (readView().open) stopWaiting = onApiReady(() => void loadOne(readView().open!));
    const onFocus = (ev: Event) => {
      const detail = (ev as CustomEvent<GraphFocus>).detail;
      if (!detail?.id) return;
      takePendingGraphFocusTarget();
      open(detail);
    };
    const onDeleted = (ev: Event) => {
      const detail = (ev as CustomEvent<{ id?: string }>).detail;
      if (!detail?.id) return;
      loadGen.current += 1;
      void refreshList();
      if (readView().open === detail.id) writeView({ open: "" });
      setDraft((cur) => (cur?.id === detail.id ? null : cur));
    };
    window.addEventListener("ducky:focus-graph", onFocus);
    window.addEventListener("ducky:graph-deleted", onDeleted);
    return () => {
      stopWaiting();
      window.removeEventListener("ducky:focus-graph", onFocus);
      window.removeEventListener("ducky:graph-deleted", onDeleted);
    };
  }, [loadOne, refreshList, setDraft]);

  const saveQueue = useRef<Promise<unknown>>(Promise.resolve());
  /** The draft as the server last had it (opened or saved) and saves not answered yet: a
   *  change made outside this canvas (an AI's save, another window) loads in only when
   *  nothing here is unsaved, and play never sends an old copy over it. */
  const synced = useRef<AutomationDto | null>(null);
  const savesPending = useRef(0);
  const [textSaveError, setTextSaveError] = useState("");
  /** The banner under the toolbar: the server's reason when it turned the save down. */
  const saveFailed = (error: unknown) => setTextSaveError(error instanceof SaveRefused ? `Could not save changes. ${error.message}` : "Could not save changes. Use Save to retry.");
  const readOnly = !!draft?.owner?.readOnly;
  const persist = useCallback((next: AutomationDto, owner = "") => {
    const api = getApi();
    const gen = loadGen.current;
    savesPending.current += 1;
    const operation = saveQueue.current.then(async () => {
    if (next.id && next.owner?.readOnly) return next;  // someone else's team workflow: never pushed from here
    // The list files workflows (set_workflow_folder); an open canvas must not undo that.
    const { folder, ...body } = next;
    const res = await api?.save_workflow?.(next.id ? body : { ...body, folder: folder || "" }, owner || undefined);
    const row = res?.workflow;
    if (row) {
      if (row.owner?.kind === "team") queueTeamSync();
      if (gen === loadGen.current) {
        acknowledgeDraft((current) => {
          if (next.id && current !== next) return current;
          synced.current = row;
          return row;
        });
        setSelectedId(row.id);
      }
      await refreshList();
      return row;
    }
    throw res?.error ? new SaveRefused(res.error) : new Error("Could not save workflow");
    }).finally(() => { savesPending.current -= 1; });
    saveQueue.current = operation.catch(() => undefined);
    return operation;
  }, [refreshList, acknowledgeDraft]);
  /** Before a run: save what is only on this canvas, else just wait for saves on their way. */
  const saveBeforeRun = (doc: AutomationDto) => history.current.current === synced.current ? saveQueue.current : persist(doc);
  const listedUpdated = rows.find((row) => row.id === draft?.id)?.updated || 0;
  useEffect(() => {
    const seen = synced.current;
    if (!seen?.id || seen.owner?.readOnly || listedUpdated <= (seen.updated || 0) || savesPending.current) return;
    const gen = loadGen.current;
    void getApi()?.get_workflow?.(seen.id).then((res) => {
      const row = res?.workflow;
      if (!row || gen !== loadGen.current || savesPending.current) return;
      acknowledgeDraft((current) => {
        if (current !== seen) return current;  // edited here meanwhile: that edit's save wins
        synced.current = row;
        return row;
      });
    }).catch(() => undefined);
  }, [listedUpdated, acknowledgeDraft]);

  // No workflows yet: a few ready-made pipelines to start from in one click.
  const [featured, setFeatured] = useState<AutomationTemplateDto[]>([]);
  const noWorkflows = !rows.length;
  useEffect(() => {
    if (!noWorkflows || featured.length) return;
    let alive = true;
    void Promise.resolve(getApi()?.list_workflow_templates?.()).then((res) => {
      const all = res?.templates || [];
      if (alive) setFeatured(FEATURED_TEMPLATES.map((id) => all.find((row) => row.id === id && row.ready !== false)).filter((row): row is AutomationTemplateDto => !!row));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [noWorkflows, featured.length]);

  const createNew = (ownerId: string, folder = "") => {
    setPickerOwner(ownerId);
    setPickerFolder(folder);
    setPickerOpen(true);
  };

  /** Show a folder now; the host has it (or is making it) for the next refresh. */
  const showFolder = (ownerId: string, path: string) => {
    const clean = normalizeFolder(path);
    if (clean) setEmptyFolders((current) => (current[ownerId] || []).includes(clean) ? current : { ...current, [ownerId]: [...(current[ownerId] || []), clean] });
  };

  /** New folder: kept by the host, so it stays empty and a team's reaches every member. */
  const rememberFolder = async (ownerId: string, path: string) => {
    showFolder(ownerId, path);
    const res = await getApi()?.add_workflow_folder?.(ownerId, normalizeFolder(path));
    if (res?.ok === false) {
      setActionError(res.error || "Could not make folder");
      setEmptyFolders((current) => ({ ...current, [ownerId]: (current[ownerId] || []).filter((item) => item !== normalizeFolder(path)) }));
      return;
    }
    if (ownerId !== LOCAL_OWNER.id) queueTeamSync();
    if (res) await refreshList();
  };

  /** Drag in the list (or Move to folder): file one workflow. Its old folder stays put. */
  const moveWorkflow = async (id: string, ownerId: string, folder: string) => {
    const from = rows.find((row) => row.id === id)?.folder || "";
    await saveQueue.current;
    const res = await getApi()?.set_workflow_folder?.(id, folder);
    if (!res?.workflow) { setActionError(res?.error || "Could not move workflow"); return; }
    if (ownerId !== LOCAL_OWNER.id) queueTeamSync();
    setActionError("");
    showFolder(ownerId, from);  // the folder it left stays
    acknowledgeDraft((current) => current && current.id === id ? { ...current, folder: res.workflow?.folder ?? folder } : current);
    await refreshList();
  };

  /** Rename, move or remove (move into its parent) a folder with everything in it. */
  const moveFolder = async (ownerId: string, path: string, newPath: string) => {
    await saveQueue.current;
    const res = await getApi()?.move_workflow_folder?.(ownerId, path, newPath);
    if (!res || res.ok === false) { setActionError(res?.error || "Could not move folder"); return; }
    if (ownerId !== LOCAL_OWNER.id) queueTeamSync();
    setActionError("");
    setEmptyFolders((current) => ({ ...current, [ownerId]: [...new Set((current[ownerId] || []).map((item) => movedPath(item, path, newPath) ?? item).filter(Boolean))] }));
    acknowledgeDraft((current) => {
      if (!current || (current.owner?.id || LOCAL_OWNER.id) !== ownerId) return current;
      const moved = movedPath(current.folder || "", path, newPath);
      return moved === null ? current : { ...current, folder: moved };
    });
    await refreshList();
  };

  // Right-click actions in the list. The open workflow goes through the draft so the
  // canvas, undo and saves stay in step; any other one is loaded and saved as it is.
  const workflowDoc = async (id: string): Promise<AutomationDto | undefined> => draft?.id === id ? draft : (await getApi()?.get_workflow?.(id))?.workflow;

  /** Save a workflow that isn't the open one. It is never refiled from here. */
  const saveOther = async (doc: AutomationDto) => {
    const { folder: _folder, ...body } = doc;
    await saveQueue.current;
    const res = await getApi()?.save_workflow?.(body);
    if (!res?.workflow) { setActionError(res?.error || "Could not save workflow"); return; }
    if (res.workflow.owner?.kind === "team") queueTeamSync();
    setActionError("");
    await refreshList();
  };

  const updateWorkflow = async (id: string, patch: Partial<Pick<AutomationDto, "name" | "enabled">>) => {
    if (draft?.id === id) {
      const next = { ...draft, ...patch };
      setDraft(next);
      await persist(next).catch((error: Error) => setActionError(error.message));
      return;
    }
    const doc = await workflowDoc(id);
    if (doc) await saveOther({ ...doc, ...patch });
  };

  /** A copy beside it (same owner and folder); a read-only team one is copied to Local. */
  const duplicateWorkflow = async (id: string) => {
    const doc = await workflowDoc(id);
    if (!doc) return;
    const shared = !doc.owner?.readOnly;
    await saveQueue.current;
    const res = await getApi()?.save_workflow?.({
      ...doc, id: "", name: `${doc.name || "Untitled"} copy`, owner: undefined,
      folder: shared ? rows.find((row) => row.id === id)?.folder ?? doc.folder ?? "" : "",
    }, shared ? doc.owner?.id || LOCAL_OWNER.id : LOCAL_OWNER.id);
    if (!res?.workflow) { setActionError(res?.error || "Could not duplicate workflow"); return; }
    if (res.workflow.owner?.kind === "team") queueTeamSync();
    setActionError("");
    await refreshList();
  };

  /** Deleting always asks first, in the app's own dialog; several picked ones ask once.
   *  A team's workflow goes for every member, and the dialog says so. */
  const deleteWorkflows = async (ids: string[]) => {
    if (!ids.length) return;
    const owner = (id: string) => draft?.id === id ? draft.owner : rows.find((item) => item.id === id)?.owner;
    const name = (id: string) => (draft?.id === id ? draft.name : rows.find((item) => item.id === id)?.name) || "this workflow";
    const teams = [...new Set(ids.map(owner).filter((item) => item?.kind === "team").map((item) => item!.label))];
    const listed = ids.slice(0, 5).map((id) => `“${name(id)}”`).join(", ") + (ids.length > 5 ? ` and ${ids.length - 5} more` : "");
    const ok = await confirm(ids.length === 1 && teams.length
      ? { title: `Delete for everyone in ${teams[0]}?`, message: `Every member loses “${name(ids[0])}”. You can't undo this.`, confirmLabel: "Delete", danger: true }
      : ids.length === 1
        ? { title: `Delete “${name(ids[0])}”?`, message: "You can't undo this.", confirmLabel: "Delete", danger: true }
        : { title: `Delete ${ids.length} workflows?`, message: `${listed}. ${teams.length ? `Members of ${teams.join(", ")} lose theirs too. ` : ""}You can't undo this.`, confirmLabel: `Delete ${ids.length}`, danger: true });
    if (ok !== true) return;
    for (const id of ids) {
      const open = draft?.id === id;
      const gen = open ? ++loadGen.current : loadGen.current;
      const res = await getApi()?.delete_workflow?.(id);
      if (res?.ok === false) { setActionError(res.error || "Could not delete workflow"); continue; }
      if (open && gen === loadGen.current) { setDraft(null); setSelectedId(""); writeView({ open: "" }); }
    }
    if (teams.length) queueTeamSync();
    await refreshList();
  };
  const deleteWorkflow = (id: string) => deleteWorkflows([id]);

  /** Copy or move a whole folder tree to another owner: asks first, saying what goes. */
  const copyFolder = async (fromId: string, path: string, toId: string, move: boolean, counts: { workflows: number; folders: number }) => {
    const all = owners.owners || [LOCAL_OWNER];
    const from = all.find((owner) => owner.id === fromId) || LOCAL_OWNER;
    const to = all.find((owner) => owner.id === toId) || LOCAL_OWNER;
    const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
    const what = [plural(counts.workflows, "workflow"), ...(counts.folders ? [plural(counts.folders, "folder")] : [])].join(" and ");
    const lines = [
      `${what} go${counts.workflows + counts.folders === 1 ? "es" : ""} along, nested as they are. Run workflow steps between them keep working.`,
      ...(to.kind === "team" ? [`Every member of ${to.label} gets ${move ? "them" : "the copy"}.`] : []),
      ...(move && from.kind === "team" ? [`Members of ${from.label} lose them.`] : []),
    ];
    const ok = await confirm({ title: `${move ? "Move" : "Copy"} “${folderName(path)}” to ${ownerName(to)}?`, message: lines.join(" "), confirmLabel: move ? "Move" : "Copy" });
    if (ok !== true) return;
    await saveQueue.current;
    const res = await getApi()?.copy_workflow_folder?.(fromId, path, toId, "", move);
    if (!res || res.ok === false) { setActionError(res?.error || `Could not ${move ? "move" : "copy"} folder`); return; }
    if (from.kind === "team" || to.kind === "team") queueTeamSync();
    const outside = res.outside?.length || 0;
    setActionError(outside && to.kind === "team"
      ? `${plural(outside, "Run workflow step")} in “${folderName(path)}” call${outside === 1 ? "s" : ""} workflows outside it, which members of ${to.label} may not have.`
      : "");
    if (move) {
      setEmptyFolders((current) => ({ ...current, [fromId]: (current[fromId] || []).filter((item) => !isInside(item, path)) }));
      // Same ids after a move: the open workflow now belongs to its new owner.
      if (draft?.id && res.workflows?.some((row) => row.id === draft.id)) await loadOne(draft.id);
    }
    await refreshList();
  };

  /** Remove a folder: asks, then its workflows move up a level. */
  const removeFolder = async (ownerId: string, path: string) => {
    const ok = await confirm({ title: `Remove folder “${folderName(path)}”?`, message: "Its workflows move up a level. Nothing is deleted.", confirmLabel: "Remove" });
    if (ok === true) await moveFolder(ownerId, path, parentFolder(path));
  };

  /** Delete buttons in the details panel ask first; Delete on the canvas doesn't (Ctrl+Z undoes it). */
  const confirmDelete = async (title: string, confirmLabel: string, run: () => void) => {
    const ok = await confirm({ title, message: "Ctrl+Z brings it back.", confirmLabel });
    if (ok === true) run();
  };

  const createFromTemplate = useCallback(
    async (template: AutomationTemplateDto | null, at?: { owner: string; folder: string }) => {
      const gen = ++loadGen.current;
      let created: AutomationDto;
      try {
        created = await persist({
          id: "",
          name: template?.name || "Untitled",
          description: template?.description || "",
          enabled: true,
          folder: at ? at.folder : pickerFolder,
          graph: template?.graph || emptyGraph(),
        } as AutomationDto, at ? at.owner : pickerOwner);
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Could not create workflow");
        return;
      }
      if (gen !== loadGen.current) return;
      setSelectedNodeIds([]);
      setSelectedEdge(null);
      setSpawn(null);
      setLog(null);
      setMade({});
      setActionError("");
      if (created.id) setSelectedId(created.id);
    },
    [persist, pickerOwner, pickerFolder],
  );

  /** Copy (new id) or move a workflow to Local or a team. */
  const sendTo = async (value: string) => {
    if (!draft?.id) return;
    const [action, target] = [value.slice(0, 4), value.slice(5)];
    if (action === "fold") { await moveWorkflow(draft.id, draft.owner?.id || LOCAL_OWNER.id, target); return; }
    const from = draft.owner || LOCAL_OWNER;
    const to = owners.owners?.find((owner) => owner.id === target) || LOCAL_OWNER;
    if (action === "move" && from.kind === "team") {
      const ok = await confirm({ title: `Move out of ${from.label}?`, message: `Members of ${from.label} will lose this workflow. It moves to ${ownerName(to)}.`, confirmLabel: "Move" });
      if (ok !== true) return;
    }
    await saveQueue.current;
    const res = await getApi()?.copy_workflow?.(draft.id, target, action === "move");
    if (!res?.workflow) { setActionError(res?.error || "Could not copy workflow"); return; }
    setActionError("");
    await refreshList();
    await loadOne(res.workflow.id);
  };

  const graph = draft?.graph || emptyGraph();
  const overview = zoom < OVERVIEW_ZOOM;
  const nodesById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const incomingIds = useMemo(() => new Set(graph.edges.filter((edge) => edge.kind !== "data").map((edge) => edge.target)), [graph.edges]);
  const outgoingIds = useMemo(() => new Set(graph.edges.filter((edge) => edge.kind !== "data").map((edge) => edge.source)), [graph.edges]);
  const fedPins = useMemo(() => new Set(graph.edges.filter((edge) => edge.kind === "data").flatMap((edge) => [`${edge.target}<${edge.target_pin}`, `${edge.source}>${edge.source_pin}`])), [graph.edges]);
  const faces = useNodeFaces(graph.nodes.some((node) => node.type === "pipeline.agent"));

  const patchGraph = (fn: (g: AutomationGraphDto) => AutomationGraphDto) => {
    if (!draft || readOnly) return;
    setDraft((current) => current ? { ...current, graph: cleanGroups(fn(current.graph)) } : current);
  };

  const updateNodeText = (id: string, patch: { label?: string; description?: string }) => {
    if (!draft || readOnly || nodeLocked(draft.graph, id)) return;
    const next = { ...draft, graph: { ...draft.graph, nodes: draft.graph.nodes.map((node) => node.id === id ? { ...node, ...patch } : node) } };
    setDraft(next);
    setTextSaveError("");
    void persist(next).catch(saveFailed);
  };

  const nameOf = (id: string) => { const node = nodesById.get(id); return node ? nodeLabel(node, byType.get(node.type)) : "It"; };
  const lockedNote = (ids: string[], verb: string) => ids.length === 1
    ? `${nameOf(ids[0])} is locked, so it can't be ${verb}. Unlock it in its details.`
    : `${ids.length} of them are locked, so they can't be ${verb}. Unlock them in their details.`;
  const lockedEdge = (edge: AutomationGraphEdgeDto | undefined) => edge ? [edge.source, edge.target].find((id) => nodeLocked(graph, id)) : undefined;
  const setLocked = (targets: { nodes?: string[]; groups?: string[] }, locked: boolean) => saveGraphChange((current) => {
    const lock = <T extends { locked?: boolean }>(item: T): T => {
      const { locked: _old, ...rest } = item;
      return (locked ? { ...rest, locked: true } : rest) as T;
    };
    return {
      ...current,
      nodes: current.nodes.map((node) => targets.nodes?.includes(node.id) ? lock(node) : node),
      ...(current.groups ? { groups: current.groups.map((group) => targets.groups?.includes(group.id) ? lock(group) : group) } : {}),
    };
  });

  const setNodeIcon = (id: string, icon: string) => !nodeLocked(graph, id) && saveGraphChange((current) => ({ ...current, nodes: current.nodes.map((node) => {
    if (node.id !== id) return node;
    const { icon: _old, ...rest } = node;
    return icon ? { ...rest, icon } : rest;
  }) }));

  const setNodeColor = (id: string, color: string) => !nodeLocked(graph, id) && saveGraphChange((current) => ({ ...current, nodes: current.nodes.map((node) => {
    if (node.id !== id) return node;
    const { color: _old, ...rest } = node;
    return color ? { ...rest, color } : rest;
  }) }));

  const openSpawn = (at: SpawnAt) => {
    setSpawn(at);
    setSpawnFilter("");
    if (!catalog.length) void loadCatalog();
  };

  const removeNodes = (ids: string[]) => {
    const locked = ids.filter((id) => nodeLocked(graph, id));
    if (locked.length) notify(lockedNote(locked, "deleted"));
    const free = ids.filter((id) => !locked.includes(id));
    if (!free.length) return;
    patchGraph((graph) => deleteNodes(graph, free));
    setSelectedNodeIds((current) => current.filter((id) => !free.includes(id)));
  };

  const disconnect = (index: number) => {
    const locked = lockedEdge(graph.edges[index]);
    if (locked) { notify(lockedNote([locked], "rewired")); return; }
    patchGraph((g) => ({ ...g, edges: g.edges.filter((_, i) => i !== index) }));
    setSelectedEdge(null);
  };

  const addNodeAt = (entry: PaletteTile, worldX: number, worldY: number) => {
    const wire = spawn?.wire;
    const draftNode: AutomationGraphNodeDto = {
      id: nid(),
      type: entry.type,
      x: 0,
      y: 0,
      config: entry.type === "pipeline.agent" ? { ducky: "__new__" } : entry.workflowId ? { workflow_id: entry.workflowId, args: {} } : entry.type === CODE_TYPE ? blankCodeConfig() : {},
      label: entry.label,
      description: entry.description || "",
    };
    const pins = nodePins(draftNode, entry, rows);
    const fit = wire?.pin ? firstFit(wire.dir === "out" ? pins.inputs : pins.outputs, wire.pinType || "any", wire.dir === "out") : undefined;
    const layout = nodeLayout(pins, overview, cardExtra(draftNode, pins));
    const anchorY = fit ? (wire!.dir === "out" ? layout.inputY[fit.id] : layout.outputY[fit.id]) ?? layout.execY : layout.execY;
    const node = { ...draftNode, x: onGrid(wire?.dir === "in" ? worldX - NODE_WIDTH : worldX), y: onGrid(wire ? worldY - anchorY : worldY) };
    const newEdge: AutomationGraphEdgeDto | null = !wire ? null
      : wire.pin ? (fit ? (wire.dir === "out" ? { source: wire.sourceId, target: node.id, kind: "data", source_pin: wire.pin, target_pin: fit.id } : { source: node.id, target: wire.sourceId, kind: "data", source_pin: fit.id, target_pin: wire.pin }) : null)
      : wire.dir === "out" ? { source: wire.sourceId, target: node.id, kind: "main" } : { source: node.id, target: wire.sourceId, kind: "main" };
    patchGraph((g) => ({
      ...g,
      nodes: [...g.nodes, node],
      edges: !newEdge ? g.edges : [...g.edges.filter((edge) => !(newEdge.kind === "data" && edge.kind === "data" && edge.target === newEdge.target && edge.target_pin === newEdge.target_pin)), newEdge],
    }));
    setSelectedNodeIds([node.id]);
    setSelectedEdge(null);
    setInspectorKey(`node:${node.id}`);
    setSpawn(null);
    setSpawnFilter("");
  };

  const saveDraft = () => {
    if (!draft) return;
    const teamId = draft.owner?.kind === "team" ? draft.owner.id : "";
    void persist(draft).then(async () => {
      setTextSaveError("");
      if (!teamId) return;
      window.clearTimeout(syncTimer.current);
      syncTimer.current = 0;
      await getApi()?.workflow_sync?.(true, teamId, true);
    }).catch(saveFailed);
  };

  const [stopping, setStopping] = useState(false);
  const runTest = async () => {
    if (!draft?.id) return;
    const gen = loadGen.current;
    setBusy(true);
    setStopping(false);
    setLogOpen(true);
    try {
      await saveBeforeRun(draft);
      const res = await runBridgeJob<AutomationRunDto>("run_workflow", [draft.id], RUN_TIMEOUT_MS);
      if (res && gen === loadGen.current) setLog(res);
    } catch (error) {
      if (gen === loadGen.current) setActionError(error instanceof Error ? error.message : "Could not run the workflow");
    } finally {
      setBusy(false);
      setStopping(false);
    }
  };
  const [runningNode, setRunningNode] = useState("");
  /** Play on one node (you pressing it is the approval for its paid steps), or "Use
   *  this" on a picture (the steps that take the picture on screen). */
  const runNode = async (nodeId: string, how: "run" | "keep" = "run") => {
    // The newest draft: code typed a moment ago reaches it just before the click.
    const doc = history.current.current || draft;
    if (!doc?.id || busy || runningNode) return;
    const gen = loadGen.current;
    setRunningNode(nodeId);
    setLogOpen(true);
    try {
      await saveBeforeRun(doc);
      const res = how === "keep"
        ? await runBridgeJob<AutomationRunDto>("keep_workflow_preview", [doc.id, nodeId], RUN_TIMEOUT_MS)
        : await runBridgeJob<AutomationRunDto>("run_workflow_node", [doc.id, nodeId, true], RUN_TIMEOUT_MS);
      if (res && gen === loadGen.current) {
        setLog(res);
        if (res.ok === false && res.error) setActionError(res.error);
      }
    } catch (error) {
      if (gen === loadGen.current) setActionError(error instanceof Error ? error.message : "Could not run the node");
    } finally {
      setRunningNode("");
    }
  };
  const stopRun = async () => {
    if (!draft?.id) return;
    setStopping(true);
    await getApi()?.stop_workflow?.(draft.id);
  };

  // Reusable workflows are nodes too: one tile per workflow with an Inputs node.
  const functionTiles = useMemo<PaletteTile[]>(() => rows.filter((row) => row.trigger?.kind === "function" && row.id !== draft?.id).map((row) => ({
    type: "workflow.call", label: row.name || "Untitled", group: "Functions", role: "action", workflowId: row.id,
    description: row.signature ? describeSignature(row.signature) : "",
  })), [rows, draft?.id]);
  // A node added from a dropped wire must be able to take it: no start after an output,
  // no Return before an input.
  const spawnDir = spawn?.wire?.dir;
  const spawnPin = spawn?.wire?.pin ? spawn.wire.pinType || "any" : "";
  const teamWorkflow = draft?.owner?.kind === "team";
  const spawnGroups = useMemo(() => groupCatalog([...catalog, ...functionTiles].filter((tile) => {
    if (tile.type === CODE_TYPE && teamWorkflow) return false;  // custom code runs in Local workflows only for now
    if (spawnPin) {
      const config = (tile as PaletteTile).workflowId ? { workflow_id: (tile as PaletteTile).workflowId } : tile.type === CODE_TYPE ? blankCodeConfig() : {};
      const pins = nodePins({ id: "", type: tile.type, x: 0, y: 0, config }, tile, rows);
      return !!firstFit(spawnDir === "out" ? pins.inputs : pins.outputs, spawnPin as PinType, spawnDir === "out");
    }
    if (tile.exec === false && spawnDir) return false;  // a white wire can't reach a data node
    return spawnDir === "out" ? !(tile.role === "starter" || tile.type.startsWith("start.") || tile.type === "flow.input")
      : spawnDir === "in" ? !END_TYPES.has(tile.type) : true;
  }), spawnFilter), [catalog, functionTiles, spawnFilter, spawnDir, spawnPin, rows, teamWorkflow]);

  const worldFromClient = (clientX: number, clientY: number) => {
    const board = boardRef.current?.getBoundingClientRect();
    if (!board) return { x: 0, y: 0 };
    return {
      x: (clientX - board.left - pan.x) / zoom,
      y: (clientY - board.top - pan.y) / zoom,
    };
  };

  // Typed pins: a card grows a row per pin; zoomed far out it is only its title bar.
  const pinsById = useMemo(() => new Map(graph.nodes.map((node) => [node.id, nodePins(node, byType.get(node.type), rows)])), [graph.nodes, byType, rows]);
  const pinsOf = (node: AutomationGraphNodeDto): NodePins => pinsById.get(node.id) || nodePins(node, byType.get(node.type), rows);
  const layoutOf = (node: AutomationGraphNodeDto, compact = overview): NodeLayout => nodeLayout(pinsOf(node), compact, cardExtra(node, pinsOf(node)) + (live && (live.outputs[node.id] || live.logs[node.id]) ? 144 : 0));
  const nodeSize = (node?: AutomationGraphNodeDto) => ({ width: NODE_WIDTH, height: node ? layoutOf(node).height : overview ? NODE_HEIGHT_COMPACT : NODE_HEIGHT });
  const lastOutputs = log?.node_outputs || {};
  const groups = graph.groups || [];
  const titleScale = groupTitleScale(zoom);
  const groupBoxes = groups.map((group) => ({ group, depth: groupDepth(groups, group.id), members: groupMembers(groups, group.id), bounds: groupBounds(group, graph.nodes, nodeSize, groups, GROUP_TITLE_PX * titleScale) }))
    .filter((entry) => entry.bounds !== null).sort((a, b) => a.depth - b.depth);  // inner boxes paint (and click) above outer ones
  const selectionGroupable = !!draft && !readOnly && selectedNodeIds.length >= 2 && groupNodes(graph, selectedNodeIds, "probe") !== graph;

  // The details panel: a connection on its own, else every whole group in the selection
  // (outer first), then the selected nodes in the order they were picked.
  const inspectorTabs = ((): InspectorTab[] => {
    if (!draft) return [];
    const edge = selectedEdge === null ? undefined : graph.edges[selectedEdge];
    if (edge) return [{ key: `edge:${selectedEdge}`, kind: "edge", index: selectedEdge!, edge }];
    const picked = new Set(selectedNodeIds);
    return [
      ...groupBoxes.filter(({ members }) => members.length && members.every((id) => picked.has(id))).map(({ group }): InspectorTab => ({ key: `group:${group.id}`, kind: "group", group })),
      ...selectedNodeIds.flatMap((id): InspectorTab[] => { const node = nodesById.get(id); return node ? [{ key: `node:${id}`, kind: "node", node }] : []; }),
    ];
  })();

  const groupSelection = () => {
    const id = nid();
    saveGraphChange((current) => groupNodes(current, selectedNodeIds, id));
    setInspectorKey(`group:${id}`);
  };
  const isStarterNode = (node: AutomationGraphNodeDto) => byType.get(node.type)?.role === "starter" || node.type.startsWith("start.") || node.type === "flow.input";

  /** Move a group's nodes into a new reusable workflow and run it from where the group was. */
  const makeReusable = async (groupId: string) => {
    if (!draft || readOnly) return;
    if (groupLocked(draft.graph.groups || [], groupId) || groupMembers(draft.graph.groups || [], groupId).some((id) => nodeLocked(draft.graph, id))) {
      notify("Something in this group is locked, so it can't move into its own workflow. Unlock it first.");
      return;
    }
    const group = draft.graph.groups?.find((item) => item.id === groupId);
    const plan = planGroupExtraction(draft.graph, groupId, isStarterNode);
    if (!group || !plan.ok) { setActionError(plan.ok ? "That group no longer exists." : plan.error); return; }
    const gen = loadGen.current;
    const name = /^Group( \d+)?$/.test(group.name) ? `${draft.name} · ${group.name}` : group.name;
    await saveQueue.current;
    const res = await getApi()?.save_workflow?.({
      id: "", name, description: `Made from a group in ${draft.name}.`, enabled: true,
      folder: rows.find((row) => row.id === draft.id)?.folder ?? draft.folder ?? "", graph: plan.child,
    }, draft.owner?.id || LOCAL_OWNER.id);
    const created = res?.workflow;
    if (!created) { setActionError(res?.error || "Could not make the workflow"); return; }
    if (gen !== loadGen.current) return;
    const callId = nid();
    saveGraphChange((current) => {
      const again = planGroupExtraction(current, groupId, isStarterNode);
      return again.ok ? again.parent(created.id, callId, created.name || name) : current;
    });
    setSelectedNodeIds([callId]);
    setInspectorKey(`node:${callId}`);
    setActionError("");
    await refreshList();
  };

  const saveGraphChange = (fn: (current: AutomationGraphDto) => AutomationGraphDto, label?: string) => {
    if (!draft || readOnly) return;
    const nextGraph = fn(draft.graph);
    if (nextGraph === draft.graph) return;
    const next = { ...draft, graph: nextGraph };
    setDraft(next, label);
    setTextSaveError("");
    void persist(next).catch(saveFailed);
  };

  const goToEdit = (index: number) => {
    if (readOnly) return;
    const next = history.go(index);
    if (!next) return;
    setSelectedNodeIds([]); setSelectedEdge(null); setSpawn(null);
    void persist(next).then(() => setTextSaveError("")).catch(saveFailed);
  };

  const loadVersions = async () => {
    if (!draft?.id) return;
    const gen = loadGen.current;
    setVersions([]); setHistoryStatus("Loading saved versions…");
    try {
      await saveQueue.current;
      const result = await getApi()?.list_workflow_versions?.(draft.id);
      if (gen !== loadGen.current) return;
      if (!result || result.ok === false) throw new Error("Could not load versions");
      setVersions(result.versions || []); setHistoryStatus("");
    } catch { if (gen === loadGen.current) setHistoryStatus("Could not load saved versions. Reopen History to retry."); }
  };

  const restoreVersion = async (versionId: string) => {
    const current = history.current.current;
    if (!current) return;
    const gen = loadGen.current;
    try {
      const result = await getApi()?.get_workflow_version?.(current.id, versionId);
      if (gen !== loadGen.current) return;
      if (!result?.workflow || result.ok === false) throw new Error("Version not found");
      // Keep any in-flight edits in the journal and retain the current identity and run log.
      const next = { ...history.current.current!, ...editableWorkflow(result.workflow) };
      endEdit(); setDraft(next, "Restore saved version");
      setSelectedNodeIds([]); setSelectedEdge(null);
      await persist(next); setTextSaveError(""); setHistoryStatus("");
    } catch (error) { if (gen === loadGen.current) { saveFailed(error); setHistoryStatus("Could not restore version."); } }
  };

  /** A node's settings or code changed in its details: into the draft. A data wire whose pin
   *  is gone goes too, except while code is typed (a half-typed pin name must not cut wires;
   *  they go when typing ends). */
  const changeNode = (next: AutomationGraphNodeDto, label?: string) => {
    if (nodeLocked(graph, next.id) || !draft || readOnly) return;
    const typing = label === "Edit code" && codeSession.current === next.id;
    setDraft((current) => {
      if (!current) return current;
      const g = { ...current.graph, nodes: current.graph.nodes.map((n) => (n.id === next.id ? next : n)) };
      const pins = nodePins(next, byType.get(next.type), rows);
      return { ...current, graph: cleanGroups(typing ? g : dropWires(g, wiresDropped(g, next.id, pins))) };
    }, label);
  };

  /** Edit as custom code / Revert to built-in: one undo step, saved at once. */
  const replaceNode = (next: AutomationGraphNodeDto, label: string) => {
    if (nodeLocked(graph, next.id)) { notify(lockedNote([next.id], "changed")); return; }
    endEdit();
    saveGraphChange((current) => {
      const g = { ...current, nodes: current.nodes.map((n) => (n.id === next.id ? next : n)) };
      return dropWires(g, wiresDropped(g, next.id, nodePins(next, byType.get(next.type), rows)));
    }, label);
  };

  /** Typing in a node's code is one undo step from focus to blur (the editor is left out of
   *  the focused-field steps below); then wires to pins it no longer has are disconnected,
   *  in the same step. */
  const codeTyping = (phase: "begin" | "end", nodeId: string) => {
    if (phase === "begin") { codeSession.current = nodeId; beginEdit(); return; }
    if (codeSession.current !== nodeId) return;
    codeSession.current = "";
    const current = history.current.current;
    const node = current?.graph.nodes.find((item) => item.id === nodeId);
    if (current && node && isCodeNode(node) && !readOnly) {
      const dropped = wiresDropped(current.graph, nodeId, nodePins(node, byType.get(node.type), rows));
      if (dropped.length) setDraft({ ...current, graph: dropWires(current.graph, dropped) }, "Edit code");
    }
    endEdit();
  };

  /** A failed custom code step in the run log: select it and open its code at that line. */
  const openCodeAt = (step: AutomationRunStepDto) => {
    if (!step.id || !nodesById.has(step.id)) return;
    setSelectedEdge(null);
    setSelectedNodeIds([step.id]);
    setInspectorKey(`node:${step.id}`);
    setDetailsTab("code");
    setCodeFocus((current) => ({ nodeId: step.id!, line: step.code_error?.line || 1, nonce: (current?.nonce || 0) + 1 }));
    fitView([step.id], true);
  };

  /** Each node's newest step in the shown run: its inputs, log and code error. */
  const lastSteps = useMemo(() => {
    const out: Record<string, AutomationRunStepDto> = {};
    for (const step of log?.steps || []) if (step.id) out[step.id] = step;  // a loop's later pass wins
    return out;
  }, [log]);

  const onGraphKeyDown = (event: React.KeyboardEvent) => {
    // Typing fields and the code editor keep their own keys (Ctrl+Z undoes text there, not the graph).
    if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false']), .monaco-editor")) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === "z" || key === "y") && draft) {
      event.preventDefault(); event.stopPropagation();
      goToEdit(history.index + (key === "y" || event.shiftKey ? 1 : -1));
      return;
    }
    if ((event.ctrlKey || event.metaKey) && !event.altKey && key === "g" && draft) {
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      const locked = selectedNodeIds.filter((id) => nodeLocked(graph, id));
      if (event.shiftKey && locked.length) notify(lockedNote(locked, "taken out of a group"));
      else if (event.shiftKey) saveGraphChange((current) => ungroupNodes(current, selectedNodeIds));
      else groupSelection();
    } else if ((event.key === "Delete" || event.key === "Backspace") && draft && event.target instanceof Node && boardRef.current?.contains(event.target)) {
      event.preventDefault();
      if (selectedEdge !== null) disconnect(selectedEdge);
      else if (selectedNodeIds.length) removeNodes(selectedNodeIds);
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && (key === "v" || key === "h")) {
      setTool(key === "v" ? "select" : "hand");
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && key === "f" && draft) {
      event.preventDefault();
      fitView();
    } else if (event.key === " " && event.target === boardRef.current) {
      event.preventDefault();
      if (!event.repeat) setSpaceHand(true);
    } else if (event.key === "Escape") {
      endEdit();
      setSelectedEdge(null);
      if (marqueeRef.current) setSelectedNodeIds(marqueeRef.current.base);
      else setSelectedNodeIds([]);
      marqueeRef.current = null;
      setMarquee(null);
      dragRef.current = null;
    }
  };

  const toggleNodeSelection = (event: React.PointerEvent, id: string) => {
    if (event.button !== 0 || !(event.ctrlKey || event.metaKey) || event.shiftKey) return;
    if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) return;
    event.preventDefault();
    event.stopPropagation();
    boardRef.current?.focus({ preventScroll: true });
    setSelectedEdge(null);
    if (!selectedNodeIds.includes(id)) setInspectorKey(`node:${id}`);
    setSelectedNodeIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  /** `lockedLabel` names a locked group being dragged (else the locked nodes are named). */
  /** One grid square in canvas units (Appearance → Workflows: Grid spacing). */
  const gridStep = () => {
    const size = boardRef.current ? parseFloat(window.getComputedStyle(boardRef.current).getPropertyValue("--wf-grid-size")) : NaN;
    return Number.isFinite(size) && size > 0 ? size : 16;
  };
  const onGrid = (value: number) => snap ? Math.round(value / gridStep()) * gridStep() : value;

  /** Press on a node or group: dragging moves it (and whatever is selected with it) without
   *  selecting anything; letting go without moving is a click, which selects it. */
  const startSelectionDrag = (event: React.PointerEvent, ids: string[], clickIds = ids, lockedLabel = "", clickKey = "") => {
    event.preventDefault();
    event.stopPropagation();
    boardRef.current?.focus({ preventScroll: true });
    beginEdit();
    const locked = ids.filter((id) => nodeLocked(graph, id));
    dragRef.current = {
      start: worldFromClient(event.clientX, event.clientY),
      origins: graph.nodes.filter((node) => ids.includes(node.id) && !locked.includes(node.id)).map(({ id, x, y }) => ({ id, x, y })),
      clickIds, clickKey, moved: false, grid: snap ? gridStep() : 0,
      blocked: !locked.length ? "" : lockedLabel ? `${lockedLabel} is locked, so it can't be moved. Unlock it in its details.` : lockedNote(locked, "moved"),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const updateMarquee = (clientX: number, clientY: number) => {
    const selection = marqueeRef.current;
    if (!selection) return;
    const bounds = selectionRect(selection.start, worldFromClient(clientX, clientY));
    setMarquee(bounds);
    setSelectedNodeIds([...new Set([...selection.base, ...graph.nodes.filter((node) => intersects(bounds, { x: node.x, y: node.y, ...nodeSize(node) })).map((node) => node.id)])]);
  };

  const onBoardWheel = (e: React.WheelEvent) => {
    if (e.target instanceof Element && e.target.closest(".aw-log-dock")) return;
    e.preventDefault();
    stopGlide();
    const next = clampZoom(zoom * (e.deltaY < 0 ? 1.08 : 0.92));
    const box = boardRef.current?.getBoundingClientRect();
    if (box) setPan(zoomAt(pan, zoom, next, { x: e.clientX - box.left, y: e.clientY - box.top }));
    setZoom(next);
  };

  const copyLog = useCallback(
    (override?: string) => {
      const text = override || formatRunLog(log, draft?.name);
      if (!text) return;
      void copyText(text).then((ok) => {
        if (!ok) return;
        setLogCopied(true);
        window.setTimeout(() => setLogCopied(false), 2000);
      });
    },
    [log, draft?.name],
  );

  const onLogResizeDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    logResizeRef.current = { startY: e.clientY, startH: logHeight };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onLogResizeMove = (e: React.PointerEvent) => {
    const drag = logResizeRef.current;
    if (!drag) return;
    e.stopPropagation();
    setLogHeight(clampLogHeight(drag.startH + (drag.startY - e.clientY), boardRef.current?.clientHeight || 0));
  };

  const onLogResizeUp = () => {
    logResizeRef.current = null;
  };

  const cancelHold = () => {
    if (!holdRef.current) return;
    window.clearTimeout(holdRef.current.timer);
    holdRef.current = null;
  };
  useEffect(() => () => { if (holdRef.current) window.clearTimeout(holdRef.current.timer); }, []);
  const startHold = (e: React.PointerEvent) => {
    cancelHold();
    if (!draft) return;
    const { clientX, clientY, pointerId } = e;
    const world = worldFromClient(clientX, clientY);
    holdRef.current = { pointerId, x: clientX, y: clientY, timer: window.setTimeout(() => {
      holdRef.current = null;
      panRef.current = null;
      if (marqueeRef.current) { setSelectedNodeIds(marqueeRef.current.base); marqueeRef.current = null; setMarquee(null); }
      suppressMenuUntil.current = Date.now() + 700;  // the context menu a long touch fires next
      openSpawn(spawnAtPoint(clientX, clientY, world));
    }, HOLD_MS) };
  };

  const onBoardPointerDown = (e: React.PointerEvent) => {
    stopGlide();
    if (marqueeRef.current || pinch.current || (e.target instanceof Element && e.target.closest("button, input, textarea, select, .aw-edge"))) return;
    // The selection clears on release, and only if the canvas wasn't dragged: panning keeps it.
    if (e.button === 0) {
      setWireFrom(null);
      setSpawn(null);
    }
    if (e.button === 0 && activeTool === "select" && draft) {
      // Select tool: drag out a box. Ctrl or Shift adds to what is already selected.
      e.preventDefault();
      boardRef.current?.focus({ preventScroll: true });
      marqueeRef.current = { pointerId: e.pointerId, start: worldFromClient(e.clientX, e.clientY), base: e.ctrlKey || e.metaKey || e.shiftKey ? selectedNodeIds : [] };
      setSelectedEdge(null);
      setMarquee({ ...marqueeRef.current.start, width: 0, height: 0 });
      e.currentTarget.setPointerCapture(e.pointerId);
    } else if ([0, 1, 2].includes(e.button)) {
      e.preventDefault();
      panRef.current = { x: pan.x, y: pan.y, px: e.clientX, py: e.clientY, button: e.button, moved: false };
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    if (e.button === 0) startHold(e);
  };

  const onBoardPointerCapture = (e: React.PointerEvent) => {
    if (draft && e.button === 0 && (e.ctrlKey || e.metaKey) && e.shiftKey && !(e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])"))) {
      e.preventDefault(); e.stopPropagation();
      boardRef.current?.focus({ preventScroll: true });
      marqueeRef.current = { pointerId: e.pointerId, start: worldFromClient(e.clientX, e.clientY), base: selectedNodeIds };
      dragRef.current = null; panRef.current = null;
      setSelectedEdge(null); setSpawn(null);
      setMarquee({ ...marqueeRef.current.start, width: 0, height: 0 });
      e.currentTarget.setPointerCapture(e.pointerId);
      return;
    }
    if (e.pointerType !== "touch" || (e.target instanceof Element && e.target.closest(".aw-log-dock, .choice-dropdown-menu, input, textarea, select"))) return;
    touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (touches.current.size !== 2) return;
    const [a, b] = [...touches.current.values()];
    const box = boardRef.current!.getBoundingClientRect();
    pinch.current = { distance: Math.max(1, Math.hypot(b.x - a.x, b.y - a.y)), center: { x: (a.x + b.x) / 2 - box.left, y: (a.y + b.y) / 2 - box.top }, pan, zoom };
    cancelHold();
    if (marqueeRef.current) { setSelectedNodeIds(marqueeRef.current.base); marqueeRef.current = null; setMarquee(null); }
    dragRef.current = null; panRef.current = null; wireRef.current = null;
    setWireFrom(null); setDraftWire(null);
    e.currentTarget.setPointerCapture(e.pointerId);
    e.stopPropagation();
  };

  const onBoardPointerMove = (e: React.PointerEvent) => {
    e.stopPropagation();
    if (holdRef.current && holdRef.current.pointerId === e.pointerId && Math.hypot(e.clientX - holdRef.current.x, e.clientY - holdRef.current.y) > 8) cancelHold();
    if (marqueeRef.current) {
      if (e.pointerId === marqueeRef.current.pointerId) updateMarquee(e.clientX, e.clientY);
      return;
    }
    if (touches.current.has(e.pointerId)) touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.current && touches.current.size >= 2) {
      const [a, b] = [...touches.current.values()];
      const box = boardRef.current!.getBoundingClientRect();
      const start = pinch.current;
      const next = clampZoom(start.zoom * Math.hypot(b.x - a.x, b.y - a.y) / start.distance);
      const anchored = zoomAt(start.pan, start.zoom, next, start.center);
      setZoom(next);
      setPan({ x: anchored.x + (a.x + b.x) / 2 - box.left - start.center.x, y: anchored.y + (a.y + b.y) / 2 - box.top - start.center.y });
      return;
    }
    if (wireRef.current) {
      const w = worldFromClient(e.clientX, e.clientY);
      setDraftWire((d) => (d ? { ...d, toX: w.x, toY: w.y } : d));
      return;
    }
    if (panRef.current) {
      if (Math.hypot(e.clientX - panRef.current.px, e.clientY - panRef.current.py) > 5) panRef.current.moved = true;
      setPan({
        x: panRef.current.x + (e.clientX - panRef.current.px),
        y: panRef.current.y + (e.clientY - panRef.current.py),
      });
      return;
    }
    const drag = dragRef.current;
    if (!drag || !draft) return;
    const point = worldFromClient(e.clientX, e.clientY);
    let dx = point.x - drag.start.x, dy = point.y - drag.start.y;
    if (Math.hypot(dx, dy) * zoom > 3) drag.moved = true;
    if (!drag.moved) return;
    if (drag.blocked && !drag.warned) { drag.warned = true; notify(drag.blocked); }
    if (!drag.origins.length) return;
    if (drag.grid) {
      // The node you grabbed lands on the grid; the rest keep their places around it.
      const anchor = drag.origins.find((node) => node.id === drag.clickIds[0]) || drag.origins[0];
      dx = Math.round((anchor.x + dx) / drag.grid) * drag.grid - anchor.x;
      dy = Math.round((anchor.y + dy) / drag.grid) * drag.grid - anchor.y;
    }
    const origins = new Map(drag.origins.map((node) => [node.id, node]));
    patchGraph((g) => ({ ...g, nodes: g.nodes.map((node) => {
      const origin = origins.get(node.id);
      return origin ? { ...node, x: origin.x + dx, y: origin.y + dy } : node;
    }) }));
  };

  const connectNodes = (sourceId: string, targetId: string) => {
    if (!sourceId || !targetId || sourceId === targetId || byType.get(nodesById.get(targetId)?.type || "")?.role === "starter") return;
    const source = nodesById.get(sourceId);
    if (source && isEndNode(source)) return;
    const target = nodesById.get(targetId);
    const loose = [source, target].find((node) => node && !pinsOf(node).exec);
    if (loose) { notify(`${nameOf(loose.id)} has no white pins: it runs when its value is needed. Wire its coloured pins instead.`); return; }
    const locked = [sourceId, targetId].find((id) => nodeLocked(graph, id));
    if (locked) { notify(lockedNote([locked], "rewired")); return; }
    patchGraph((g) => {
      const exists = g.edges.some((x) => x.source === sourceId && x.target === targetId);
      return exists ? g : { ...g, edges: [...g.edges, { source: sourceId, target: targetId, kind: "main" }] };
    });
  };

  /** A data wire from an output pin to an input pin; a new wire into a fed input replaces the old one. */
  const connectData = (from: { node: string; pin: string }, to: { node: string; pin: string }) => {
    const source = nodesById.get(from.node), target = nodesById.get(to.node);
    if (!source || !target || source.id === target.id) return;
    const out = pinsOf(source).outputs.find((pin) => pin.id === from.pin);
    const into = pinsOf(target).inputs.find((pin) => pin.id === to.pin);
    if (!out || !into) return;
    if (!accepts(cleanType(into.type), cleanType(out.type))) { notify(`${out.label} (${out.type}) can't go into ${into.label} (${into.type}).`); return; }
    const locked = [source.id, target.id].find((id) => nodeLocked(graph, id));
    if (locked) { notify(lockedNote([locked], "rewired")); return; }
    patchGraph((g) => ({ ...g, edges: [
      ...g.edges.filter((edge) => !(edge.kind === "data" && edge.target === target.id && edge.target_pin === into.id)),
      { source: source.id, target: target.id, kind: "data", source_pin: out.id, target_pin: into.id },
    ] }));
  };

  /** Dropping a data wire: on a pin, on a node (its first pin that fits), or on empty canvas. */
  const finishDataWire = (w: WireStart & { fromX: number; fromY: number }, clientX: number, clientY: number) => {
    const type = w.pinType || "any";
    for (const el of document.elementsFromPoint(clientX, clientY)) {
      if (!(el instanceof Element)) continue;
      const pinEl = el.closest<HTMLElement>("[data-aw-pin]");
      if (pinEl) {
        if (pinEl.dataset.awPin === w.dir || pinEl.dataset.awNode === w.sourceId) return;
        const end = { node: pinEl.dataset.awNode || "", pin: pinEl.dataset.awPinId || "" };
        if (w.dir === "out") connectData({ node: w.sourceId, pin: w.pin! }, end); else connectData(end, { node: w.sourceId, pin: w.pin! });
        return;
      }
      const id = el.closest("[data-aw-node]")?.getAttribute("data-aw-node") || "";
      if (id && id !== w.sourceId) {
        const node = nodesById.get(id);
        const fit = node ? firstFit(w.dir === "out" ? pinsOf(node).inputs : pinsOf(node).outputs, type, w.dir === "out") : undefined;
        if (!node || !fit) { notify(`${nameOf(id)} has no ${w.dir === "out" ? "input" : "output"} that fits ${type}.`); return; }
        if (w.dir === "out") connectData({ node: w.sourceId, pin: w.pin! }, { node: id, pin: fit.id }); else connectData({ node: id, pin: fit.id }, { node: w.sourceId, pin: w.pin! });
        return;
      }
    }
    const drop = worldFromClient(clientX, clientY);
    if (Math.hypot(drop.x - w.fromX, drop.y - w.fromY) * zoom < 16) return;  // a click on the pin
    if (nodeLocked(graph, w.sourceId)) { notify(lockedNote([w.sourceId], "rewired")); return; }
    openSpawn(spawnAtPoint(clientX, clientY, drop, { sourceId: w.sourceId, dir: w.dir, pin: w.pin, pinType: type }));
  };

  const finishWire = (clientX: number, clientY: number) => {
    const w = wireRef.current;
    wireRef.current = null;
    setWireFrom(null);
    setDraftWire(null);
    if (!w) return;
    if (w.pin) { finishDataWire(w, clientX, clientY); return; }
    const stack = document.elementsFromPoint(clientX, clientY);
    let tid = "";
    for (const el of stack) {
      if (!(el instanceof Element)) continue;
      const port = el.closest("[data-aw-port]");
      if (port && port.getAttribute("data-aw-port") === w.dir) return;
      const nodeEl = el.closest("[data-aw-node]");
      const id = nodeEl?.getAttribute("data-aw-node") || "";
      if (id && id !== w.sourceId) {
        tid = id;
        break;
      }
    }
    if (!tid) {
      const drop = worldFromClient(clientX, clientY);
      if (Math.hypot(drop.x - w.fromX, drop.y - w.fromY) * zoom < 16) return;  // a click on the port
      if (nodeLocked(graph, w.sourceId)) { notify(lockedNote([w.sourceId], "rewired")); return; }
      openSpawn(spawnAtPoint(clientX, clientY, drop, { sourceId: w.sourceId, dir: w.dir }));
      return;
    }
    if (w.dir === "out") connectNodes(w.sourceId, tid);
    else connectNodes(tid, w.sourceId);
  };

  const endPointer = (e: React.PointerEvent) => {
    e.stopPropagation();
    cancelHold();
    endEdit();
    if (marqueeRef.current) {
      if (e.pointerId !== marqueeRef.current.pointerId) return;
      if (e.type === "pointercancel") setSelectedNodeIds(marqueeRef.current.base);
      else updateMarquee(e.clientX, e.clientY);
      marqueeRef.current = null; setMarquee(null);
      return;
    }
    if (dragRef.current && !dragRef.current.moved && e.type !== "pointercancel") {
      const click = dragRef.current;
      setSelectedEdge(null);
      setSelectedNodeIds(click.clickIds);
      if (click.clickKey) setInspectorKey(click.clickKey);
    }
    if (e.type === "pointercancel") { touches.current.clear(); pinch.current = null; }
    touches.current.delete(e.pointerId);
    if (pinch.current) {
      pinch.current = null;
      const remaining = [...touches.current.values()][0];
      panRef.current = remaining ? { x: pan.x, y: pan.y, px: remaining.x, py: remaining.y, button: 0, moved: true } : null;
      return;
    }
    if (panRef.current?.button === 2 && panRef.current.moved) suppressMenuUntil.current = Date.now() + 500;
    if (panRef.current?.button === 0 && !panRef.current.moved && e.type !== "pointercancel") { setSelectedNodeIds([]); setSelectedEdge(null); }
    if (wireRef.current && e.type !== "pointercancel") finishWire(e.clientX, e.clientY);
    else { wireRef.current = null; setWireFrom(null); setDraftWire(null); }
    dragRef.current = null;
    panRef.current = null;
  };

  const startWire = (e: React.PointerEvent, node: AutomationGraphNodeDto, dir: "in" | "out") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const { x: fromX, y: fromY } = portPoint(node, dir, overview);
    wireRef.current = { sourceId: node.id, dir, fromX, fromY };
    setWireFrom(node.id);
    setDraftWire({ sourceId: node.id, dir, fromX, fromY, toX: fromX, toY: fromY });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const startPinWire = (e: React.PointerEvent, node: AutomationGraphNodeDto, dir: "in" | "out", pin: PinDto) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const { x: fromX, y: fromY } = pinPoint(node, layoutOf(node), dir, pin.id);
    const pinType = cleanType(pin.type);
    wireRef.current = { sourceId: node.id, dir, pin: pin.id, pinType, fromX, fromY };
    setWireFrom(node.id);
    setDraftWire({ sourceId: node.id, dir, pinType, fromX, fromY, toX: fromX, toY: fromY });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onBoardContextMenu = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    if (t.closest(".aw-log-dock")) return;
    if (t.closest("input, textarea, select")) return;
    e.preventDefault();
    if (Date.now() < suppressMenuUntil.current || (panRef.current?.button === 2 && panRef.current.moved)) return;
    if (!draft) return;
    openSpawn(spawnAtPoint(e.clientX, e.clientY, worldFromClient(e.clientX, e.clientY)));
  };

  /** + opens the menu just above itself; new nodes land in the middle of the view. */
  const openAddMenu = (button: HTMLElement) => {
    const board = boardRef.current?.getBoundingClientRect();
    if (!board) return;
    const world = worldFromClient(board.left + board.width / 2, board.top + board.height / 2);
    const rect = button.getBoundingClientRect();
    const x = Math.max(8, Math.min(rect.left + rect.width / 2 - 160, window.innerWidth - 328));
    openSpawn({ x, bottom: Math.max(8, window.innerHeight - rect.top + 10), worldX: world.x, worldY: world.y, origin: `${rect.left + rect.width / 2 - x}px 100%` });
  };

  const zoomTo = (value: string) => {
    if (value === "fit") { fitView(); return; }
    stopGlide();
    const next = clampZoom(value === "in" ? zoom * 1.2 : value === "out" ? zoom / 1.2 : Number(value));
    const box = boardRef.current?.getBoundingClientRect();
    if (box?.width && box.height) setPan(zoomAt(pan, zoom, next, { x: box.width / 2, y: box.height / 2 }));
    setZoom(next);
  };

  const startNodeDrag = (e: React.PointerEvent, node: AutomationGraphNodeDto) => {
    if (e.button !== 0) return;
    startSelectionDrag(e, selectedNodeIds.includes(node.id) ? selectedNodeIds : [node.id], [node.id], "", `node:${node.id}`);
  };

  useEffect(() => {
    const cancel = () => {
      endEdit();
      if (marqueeRef.current) setSelectedNodeIds(marqueeRef.current.base);
      marqueeRef.current = null; setMarquee(null);
      touches.current.clear(); pinch.current = null; panRef.current = null;
      dragRef.current = null; wireRef.current = null;
      setWireFrom(null); setDraftWire(null);
    };
    window.addEventListener("blur", cancel);
    return () => window.removeEventListener("blur", cancel);
  }, [endEdit]);

  /** The part of the board no panel covers (screen px from the board's corner). */
  const visibleArea = (inspectorOpen: boolean) => {
    const box = boardRef.current;
    if (!box || box.clientWidth < 120 || box.clientHeight < 120) return null;  // hidden, or still being shown
    const board = box.getBoundingClientRect();
    const sidebar = listRef.current?.getBoundingClientRect();
    const toolbar = toolbarRef.current?.getBoundingClientRect();
    const left = listCollapsed ? 24 : Math.max(24, (sidebar?.right || board.left) - board.left + 24);
    const top = Math.max(24, (toolbar?.bottom || board.top) - board.top + 24, listCollapsed ? (sidebar?.bottom || board.top) - board.top + 24 : 0);
    let right = box.clientWidth - 24;
    let bottom = box.clientHeight - (logOpen ? logHeight + 88 : 72);
    // Layout position, not the slide-in transform: the panel may still be on its way in.
    const panel = box.parentElement?.querySelector<HTMLElement>(".aw-inspector");
    if (inspectorOpen && panel?.offsetWidth) {
      if (panel.offsetLeft > box.clientWidth / 2) right = Math.min(right, panel.offsetLeft - 24);
      else bottom = Math.min(bottom, panel.offsetTop - 24);  // narrow: it is a sheet at the bottom
    }
    return { x: left, y: top, width: Math.max(80, right - left), height: Math.max(80, bottom - top) };
  };

  /** What fitting `ids` frames at a given zoom: those nodes, plus the frame and title of any
   *  group they fill. No ids: everything. */
  const frameOf = (ids: string[], atZoom: number) => {
    const compact = atZoom < OVERVIEW_ZOOM;
    const size = (node: AutomationGraphNodeDto) => ({ width: NODE_WIDTH, height: layoutOf(node, compact).height });
    const title = GROUP_TITLE_PX * groupTitleScale(atZoom);
    const all = !ids.length;
    const rects: GraphRect[] = graph.nodes.filter((node) => all || ids.includes(node.id)).map((node) => ({ x: node.x, y: node.y, ...size(node) }));
    for (const group of groups) {
      const members = groupMembers(groups, group.id);
      if (!members.length || !(all || members.every((id) => ids.includes(id)))) continue;
      const frame = groupBounds(group, graph.nodes, size, groups, title);
      if (frame) rects.push({ x: frame.x, y: frame.y - title, width: frame.width, height: frame.height + title });
    }
    if (!rects.length) return null;
    const x = Math.min(...rects.map((rect) => rect.x));
    const y = Math.min(...rects.map((rect) => rect.y));
    return { x, y, width: Math.max(...rects.map((rect) => rect.x + rect.width)) - x, height: Math.max(...rects.map((rect) => rect.y + rect.height)) - y };
  };

  /** F, the Fit button and Zoom to fit: the selection (one node zooms in on it, a group comes
   *  with its frame), or the whole workflow when nothing is selected. */
  const fitView = (ids: string[] = selectedNodeIds, inspectorOpen = inspectorTabs.length > 0, tries = 0): void => {
    const view = visibleArea(inspectorOpen);
    // The tab may be on its way in (an agent opened it): try again once it has a size.
    if (!view) { if (tries < 20) window.requestAnimationFrame(() => fitView(ids, inspectorOpen, tries + 1)); return; }
    let camera: { x: number; y: number; zoom: number } | null = null;
    let at = zoom;
    for (let pass = 0; pass < 2; pass++) {  // thin cards and smaller titles change the frame once zoomed out
      const frame = frameOf(ids, at);
      if (!frame) return;
      camera = fitCamera(frame, view, ids.length ? 1.5 : 1);
      at = camera.zoom;
    }
    if (camera) glideTo(camera);
  };

  // An agent's change or walkthrough: select what it points at (if asked), light it up,
  // glide there and show its caption.
  useEffect(() => {
    if (!pendingShow || !draft || draft.id !== pendingShow.id) return;
    setPendingShow(null);
    const ids = (pendingShow.nodes || []).filter((id) => draft.graph.nodes.some((node) => node.id === id));
    if (pendingShow.select && ids.length) {
      const whole = groupBoxes.find(({ members }) => members.length === ids.length && members.every((id) => ids.includes(id)));
      setSelectedEdge(null);
      setSelectedNodeIds(ids);
      setInspectorKey(whole ? `group:${whole.group.id}` : `node:${ids[0]}`);
    }
    if (ids.length) {
      flash(ids);
      fitView(ids, !!pendingShow.select || inspectorTabs.length > 0);
    }
    if (pendingShow.note) notify(pendingShow.note, "note");
  }, [pendingShow, draft]);

  // A walkthrough step on the canvas: glide to that node or group instead of scrolling.
  useEffect(() => {
    const onReveal = (event: Event) => {
      const id = String((event as CustomEvent<{ id?: string }>).detail?.id || "");
      if (!id.startsWith("workflows.")) return;
      event.preventDefault();  // never scrollIntoView inside the editor: it would shift the canvas
      const node = id.startsWith("workflows.node.") ? id.slice("workflows.node.".length)
        : id.startsWith("workflows.pin.") ? id.slice("workflows.pin.".length).split(".")[0] : "";
      const group = id.startsWith("workflows.group.") ? id.slice("workflows.group.".length) : "";
      const ids = node ? [node] : group ? groupMembers(groups, group) : [];
      if (ids.length) fitView(ids);
    };
    window.addEventListener("ducky:ui-target-reveal", onReveal);
    return () => window.removeEventListener("ducky:ui-target-reveal", onReveal);
  });

  // Walkthrough targets the dropdown triggers can't take a ref for.
  useEffect(() => {
    const bar = toolbarRef.current;
    const found: [string, Element | null | undefined, string][] = [
      ["workflows.list", listRef.current, "Workflows list"],
      ["workflows.toolbar.history", bar?.querySelector('[aria-label="History"]'), "History"],
      ["workflows.toolbar.move", bar?.querySelector('[aria-label="Move or copy"]'), "Move or copy"],
      ["workflows.toolbar.duplicate", bar?.querySelector('[aria-label="Duplicate"]'), "Duplicate"],
    ];
    for (const [id, el, label] of found) if (el instanceof HTMLElement) registerTarget(id, el, { route: "workflows", label, kind: "dropdown" });
    return () => { for (const [id] of found) unregisterTarget(id); };
  }, [draft?.id]);

  // The open workflow, for Tour this workflow and the AI's tour_workflow.
  useEffect(() => {
    setCurrentWorkflow(draft ? { id: draft.id, name: draft.name, graph: draft.graph, byType } : null);
  }, [draft, byType]);
  useEffect(() => () => setCurrentWorkflow(null), []);

  // The first time Workflows opens: its tour, once.
  useEffect(() => { void startFirstOpenTour(WORKFLOWS_INTRO_TOUR_ID); }, []);

  const startHelp = async (value: string) => {
    if (value !== "this") { await redoTour(value); return; }
    if (!draft) return;
    const steps = buildWorkflowTour(draft.graph, byType);
    if (steps.length) await runAgentWalkthrough(withOpenStep(steps, draft.id));
  };

  // Show me and tours: get the editor ready (open, select, menus) and find what only it can.
  const tourApi = useRef({ loadOne, fitView, openAddMenu, createNew, groups, setSpawn, setSpawnFilter, setPickerOpen, setLogOpen, setSelectedNodeIds, setInspectorKey, setSelectedEdge, draftId: draft?.id || "", owners });
  tourApi.current = { loadOne, fitView, openAddMenu, createNew, groups, setSpawn, setSpawnFilter, setPickerOpen, setLogOpen, setSelectedNodeIds, setInspectorKey, setSelectedEdge, draftId: draft?.id || "", owners };
  useEffect(() => {
    const api = () => tourApi.current;
    const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
    const waitOpen = async (id: string) => { for (let i = 0; i < 60 && api().draftId !== id; i++) await sleep(100); };
    const open = async (id: string) => {
      if (!id) return;
      requestOpenWorkflowsTab();
      if (api().draftId !== id) void api().loadOne(id);
      await waitOpen(id);
    };
    const css = cssEscape;
    const pinParts = (id: string) => {
      const [node, dir, ...pin] = id.slice("workflows.pin.".length).split(".");
      return { node, dir, pin: pin.join(".") };
    };
    const wireKey = (id: string) => id.slice("workflows.wire.".length);
    const wireNodes = (key: string) => key.split(":")[0].split(">").map((end) => end.split(".")[0]);
    const offs = [
      registerUiAction("workflows.open", (args) => open(String(args.id || "")), { label: "Open a workflow: {id}", route: "workflows" }),
      registerUiAction("workflows.select", async (args) => {
        await open(String(args.id || ""));
        const group = String(args.group_id || "");
        const ids = group ? groupMembers(api().groups, group) : Array.isArray(args.node_ids) ? args.node_ids.map(String) : [];
        if (!ids.length) return;
        api().setSelectedEdge(null);
        api().setSelectedNodeIds(ids);
        api().setInspectorKey(group ? `group:${group}` : `node:${ids[0]}`);
        api().fitView(ids, true);
      }, { label: "Select nodes: {node_ids} or {group_id} (and {id} to open the workflow)", route: "workflows" }),
      registerUiAction("workflows.add_menu", async (args) => {
        const button = getTargetElement("workflows.add");
        if (button) api().openAddMenu(button);
        if (args.query) { await sleep(50); api().setSpawnFilter(String(args.query)); }
      }, { label: "Open the add-node menu: {query}", route: "workflows" }),
      registerUiAction("workflows.templates", async (args) => {
        api().createNew((api().owners.owners || []).find((owner) => !owner.readOnly)?.id || LOCAL_OWNER.id);
        if (args.shelf) { await sleep(80); window.dispatchEvent(new CustomEvent("ducky:templates-shelf", { detail: { shelf: String(args.shelf) } })); }
      }, { label: "Open New workflow (templates): {shelf}", route: "workflows" }),
      registerUiAction("workflows.close_overlays", () => { api().setSpawn(null); api().setPickerOpen(false); }, { label: "Close the add-node menu and the template picker", route: "workflows" }),
      registerUiAction("workflows.log", (args) => api().setLogOpen(args.open !== false), { label: "Open the run log", route: "workflows" }),
      registerTargetResolver("workflows.pin.", {
        find: (id) => {
          const { node, dir, pin } = pinParts(id);
          return document.querySelector<HTMLElement>(`[data-aw-node="${css(node)}"][data-aw-pin="${css(dir)}"][data-aw-pin-id="${css(pin)}"]`);
        },
        reveal: (id) => api().fitView([pinParts(id).node]),
      }),
      registerTargetResolver("workflows.wire.", {
        find: (id) => {
          const key = wireKey(id);
          const g = document.querySelector(`[data-aw-wire="${css(key)}"]`)
            ?? Array.from(document.querySelectorAll("[data-aw-wire]")).find((el) => (el.getAttribute("data-aw-wire") || "").split(":")[0] === key.split(":")[0]);
          return (g?.querySelector(".aw-wire") as unknown as HTMLElement | null) ?? null;
        },
        reveal: (id) => api().fitView(wireNodes(wireKey(id))),
      }),
      registerTargetResolver("workflows.details.field.", {
        find: (id) => document.querySelector<HTMLElement>(`.aw-inspector [data-aw-field="${css(id.slice("workflows.details.field.".length))}"]`),
      }),
      registerTargetResolver("workflows.log.step.", {
        find: (id) => document.querySelector<HTMLElement>(`.aw-log-dock [data-aw-log-node="${css(id.slice("workflows.log.step.".length))}"]`),
        reveal: async (id) => {
          api().setLogOpen(true);
          await sleep(200);
          document.querySelector(`.aw-log-dock [data-aw-log-node="${css(id.slice("workflows.log.step.".length))}"]`)?.scrollIntoView({ block: "nearest" });
        },
      }),
    ];
    return () => offs.forEach((off) => off());
  }, []);

  // The add-node search box, for tours.
  useEffect(() => {
    const el = spawnSearchRef.current;
    if (!spawn || !el) return;
    registerTarget("workflows.add.search", el, { route: "workflows", label: "Search nodes", kind: "input" });
    return () => unregisterTarget("workflows.add.search");
  }, [spawn]);

  const logCount = log?.steps?.length || (runLogHasContent(log) ? 1 : 0);
  const activeTab = inspectorTabs.find((tab) => tab.key === inspectorKey) || inspectorTabs[0];
  const codeWideOn = codeWide && detailsTab === "code" && activeTab?.kind === "node";
  const clearLog = async () => {
    const id = draft?.id;
    setLog(null);
    setMade({});
    if (id) await getApi()?.clear_workflow_runs?.(id);
  };

  return (
    <div ref={rootRef} className={`aw-root${listCollapsed ? " is-list-collapsed" : ""}${draft ? " has-workflow" : ""}${inspectorTabs.length ? " has-inspector" : ""}${resizingPanel ? " is-resizing" : ""}`} onKeyDown={onGraphKeyDown}
      style={{ "--aw-list-w": `${panelWidths.list}px`, "--aw-insp-w": `${codeWideOn ? Math.max(panelWidths.inspector, CODE_WIDE_W) : panelWidths.inspector}px`, ...Object.fromEntries(PANELS.map((name) => [`--aw-z-${name}`, String(panelZoom[name])])) } as React.CSSProperties}
      onFocusCapture={(event) => { if (event.target.matches("input:not(.aw-spawn-search), textarea") && !event.target.closest(".aw-code-editor")) beginEdit(); }}
      onBlurCapture={(event) => { if (event.target.matches("input, textarea") && !event.target.closest(".aw-code-editor")) endEdit(); }}>
      <WorkflowList listId={sectionId + "-list"} listRef={listRef} owners={owners} rows={rows} activeId={draft ? selectedId : ""}
        collapsed={listCollapsed} nowMs={nowMs} onToggleCollapsed={() => setListCollapsed((collapsed) => !collapsed)}
        onOpen={(id) => { void loadOne(id); const width = rootRef.current?.clientWidth || 0; if (width > 0 && width <= PHONE_EDITOR_W) setListCollapsed(true); }} onCreate={createNew}
        onImportLocal={() => void getApi()?.import_local_workflows?.().then(() => refreshList())}
        emptyFolders={folderLists} onAddFolder={(ownerId, path) => void rememberFolder(ownerId, path)}
        onMoveWorkflow={(id, ownerId, folder) => void moveWorkflow(id, ownerId, folder)}
        onMoveFolder={(ownerId, path, newPath) => void moveFolder(ownerId, path, newPath)}
        onRenameWorkflow={(id, name) => void updateWorkflow(id, { name })}
        onSetEnabled={(id, enabled) => void updateWorkflow(id, { enabled })}
        onDuplicateWorkflow={(id) => void duplicateWorkflow(id)}
        onDeleteWorkflow={(id) => void deleteWorkflow(id)}
        onDeleteWorkflows={(ids) => void deleteWorkflows(ids)}
        onRemoveFolder={(ownerId, path) => void removeFolder(ownerId, path)}
        onCopyFolder={(fromId, path, toId, move, counts) => void copyFolder(fromId, path, toId, move, counts)}
        featured={rows.length ? [] : featured} showNew={!draft}
        onCreateFrom={(template) => void createFromTemplate(template, { owner: (owners.owners || []).find((owner) => !owner.readOnly)?.id || LOCAL_OWNER.id, folder: "" })} />
      {draft && !listCollapsed ? <PanelResizeHandle label="Resize workflow list" className="aw-resize--list" value={panelWidths.list} min={LIST_W.min} max={LIST_W.max} edge="right"
        onResize={(list) => setPanelWidths((current) => ({ ...current, list }))} onActive={setResizingPanel} /> : null}
      <div className="aw-main">
        {draft ? (
          <div className="aw-toolbar" ref={toolbarRef} role="toolbar" aria-label="Workflow actions" data-aw-zoom="toolbar">
              <div className="aw-toolbar-fields">
              <input
                ref={targetRef("workflows.toolbar.name", { route: "workflows", label: "Workflow name", kind: "input" })}
                className="aw-name"
                aria-label="Workflow name"
                title={ownerHelp(draft.owner)}
                value={draft.name}
                readOnly={readOnly}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                onBlur={saveDraft}
              />

              </div>
              <button type="button" className="aw-more-toggle" aria-label="Workflow actions" title="Workflow actions" aria-expanded={phoneMenu === "actions"}
                onClick={() => setPhoneMenu((open) => (open === "actions" ? "" : "actions"))}>
                <Icons.MoreHorizontal />
              </button>
              <div className={`aw-toolbar-actions${phoneMenu === "actions" ? " is-open" : ""}`} onClick={closePhoneMenuAfterPick}>
              <button
                type="button" ref={targetRef("workflows.toolbar.delete", { route: "workflows", label: "Delete workflow" })} title="Delete" aria-label="Delete" disabled={readOnly}
                onClick={() => { if (draft.id) void deleteWorkflow(draft.id); }}
              >
                <Icons.Trash />
              </button>
              <button type="button" ref={targetRef("workflows.toolbar.undo", { route: "workflows", label: "Undo" })} aria-label="Undo" title="Undo (Ctrl+Z)" disabled={readOnly || history.index <= 0} onClick={() => goToEdit(history.index - 1)}><Icons.Undo /></button>
              <button type="button" ref={targetRef("workflows.toolbar.redo", { route: "workflows", label: "Redo" })} aria-label="Redo" title="Redo (Ctrl+Y)" disabled={readOnly || history.index >= history.entries.length - 1} onClick={() => goToEdit(history.index + 1)}><Icons.Redo /></button>
              <ChoiceDropdown aria-label="History" trigger={<Icons.Clock />} hideChevron minWidth={300}
                value={"edit:" + history.index} onOpen={() => void loadVersions()}
                header={<strong>History</strong>} footer={<small>{historyStatus || "Choose an edit to revisit it, or restore a saved version."}</small>}
                options={[
                  ...history.entries.map((entry, index) => ({ value: "edit:" + index, label: entry.label, hint: index === history.index ? "Current edit" : "Edit " + index, group: "This session" })).reverse(),
                  ...versions.map((version, index) => ({ value: "version:" + version.id, label: "Version " + (versions.length - index) + " · " + version.name + (version.note ? " · " + version.note : ""), hint: new Date(version.saved_at * 1000).toLocaleString() + " · " + version.node_count + " nodes", group: "Saved versions" })),
                ]}
                onChange={(value) => { if (value.startsWith("edit:")) goToEdit(Number(value.slice(5))); else void restoreVersion(value.slice(8)); }} />
              <ChoiceDropdown aria-label="Move or copy" trigger={<Icons.Share />} hideChevron minWidth={240} value=""
                header={<strong>Move or copy</strong>} footer={<small>Moving keeps its history on this PC. A copy gets a new id.</small>}
                options={[
                  ...(readOnly ? [] : ["", ...folderPaths(buildFolderTree(rows.filter((row) => (row.owner?.id || LOCAL_OWNER.id) === (draft.owner?.id || LOCAL_OWNER.id)), folderLists[draft.owner?.id || LOCAL_OWNER.id] || []))]
                    .filter((path) => path !== normalizeFolder(rows.find((row) => row.id === draft.id)?.folder ?? draft.folder)).map((path) => ({ value: "fold:" + path, label: path ? "Move to " + path : "Move out of folders", group: "Folder" }))),
                  ...(owners.owners || [LOCAL_OWNER]).filter((owner) => owner.id !== (draft.owner?.id || LOCAL_OWNER.id) && !owner.readOnly).flatMap((owner) => [
                    { value: "copy:" + owner.id, label: "Copy to " + ownerName(owner), group: ownerName(owner) },
                    { value: "move:" + owner.id, label: "Move to " + ownerName(owner), group: ownerName(owner), disabled: readOnly },
                  ]),
                ]}
                emptyLabel="Nowhere else to put it"
                onChange={(value) => void sendTo(value)} />
              <ChoiceDropdown aria-label="Duplicate" trigger={<Icons.Copy />} hideChevron minWidth={220} value=""
                header={<strong>Duplicate to</strong>}
                options={(owners.owners || [LOCAL_OWNER]).map((owner) => ({
                  value: owner.id,
                  label: ownerName(owner),
                  disabled: !!owner.readOnly,
                  hint: owner.readOnly ? owner.reason || "Read-only" : undefined,
                }))}
                emptyLabel="Nowhere to put a copy"
                onChange={async (ownerId) => {
                  if (!draft.id) return;
                  const copy = { ...draft, id: "", name: `${draft.name} copy`, owner: undefined };
                  await persist(copy, ownerId).catch((error: Error) => setActionError(error.message));
                }} />
              <button type="button" ref={targetRef("workflows.toolbar.save", { route: "workflows", label: "Save" })} title={draft.owner?.kind === "team" ? "Save and update online" : "Save"} aria-label="Save" disabled={readOnly} onClick={saveDraft}><Icons.Save /></button>
              {draft.owner?.kind === "team" && ["start.cron", ...catalog.filter((n) => n.role === "starter" && n.plugin_id).map((n) => n.type)].some((type) => draft.graph.nodes.some((node) => node.type === type)) ? (
                <button type="button" aria-label="Run on this PC" aria-pressed={!!draft.run_here}
                  title={draft.run_here ? "This PC runs its schedule and triggers. Click to stop." : "Its schedule and triggers run on other members' PCs only. Click to run them here too."}
                  onClick={async () => { const res = await getApi()?.set_workflow_run_here?.(draft.id, !draft.run_here); if (res?.workflow) { acknowledgeDraft((current) => current && current.id === draft.id ? { ...current, run_here: !!res.workflow?.run_here } : current); await refreshList(); } }}>
                  <Icons.Monitor />
                </button>
              ) : null}
              <button type="button" ref={targetRef("workflows.toolbar.onoff", { route: "workflows", label: "On / off" })} className="aw-onoff" aria-label="Enabled" aria-pressed={draft.enabled} disabled={readOnly}
                title={draft.enabled ? "On: it runs when started. Click to turn it off." : "Off: it won't run. Click to turn it on."}
                onClick={() => { const next = { ...draft, enabled: !draft.enabled }; setDraft(next); void persist(next); }}>
                <span className={`aw-light${draft.enabled ? " is-on" : ""}`} aria-hidden="true" />
              </button>
              {busy || (live && !live.finished) ? (
                <button type="button" ref={targetRef("workflows.toolbar.run", { route: "workflows", label: "Stop the run" })} className="aw-stop" title={stopping ? "Stopping…" : "Stop: end this run now"} aria-label="Stop" disabled={stopping} onClick={() => void stopRun()}>
                  {stopping ? <span className="aw-spin"><Icons.Spinner /></span> : <Icons.Stop />}
                </button>
              ) : (
                <button type="button" ref={targetRef("workflows.toolbar.run", { route: "workflows", label: "Test run" })} title="Test" aria-label="Test" onClick={() => void runTest()} disabled={!draft.id}><Icons.Play /></button>
              )}
              </div>
          </div>
        ) : null}
        <div
          ref={(el) => { boardRef.current = el; targetRef("workflows.canvas", { route: "workflows", label: "Workflow canvas" })(el); }}
          tabIndex={-1}
          aria-label="Workflow canvas"
          className={`aw-board is-tool-${activeTool} is-grid-${gridStyle}${overview ? " is-overview" : ""}${gliding ? " is-gliding" : ""}`}
          style={{ "--aw-grid": `calc(var(--wf-grid-size) * ${gridScale(zoom)})`, "--aw-pan-x": `${pan.x}px`, "--aw-pan-y": `${pan.y}px` } as React.CSSProperties}
          onWheel={onBoardWheel}
          onPointerDown={onBoardPointerDown}
          onPointerDownCapture={onBoardPointerCapture}
          onPointerMove={onBoardPointerMove}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
          onContextMenu={onBoardContextMenu}
        >
          <div className="aw-world" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
            {groupBoxes.map(({ group, bounds, depth, members }) => bounds ? (
              <div key={group.id} ref={targetRef(`workflows.group.${group.id}`, { route: "workflows", label: `Group ${group.name}` })} data-aw-group={group.id} className={`aw-group aw-group-color--${group.color || "plain"}${groupLocked(groups, group.id) ? " is-locked" : ""}${depth ? " is-nested" : ""}${members.length && members.every((id) => selectedNodeIds.includes(id)) ? " is-selected" : ""}`}
                style={{ left: bounds.x, top: bounds.y, width: bounds.width, height: bounds.height }}
                role="button" tabIndex={0} aria-label={`Select group ${group.name}`}
                onPointerDown={(event) => {
                  if (event.button !== 0) return;
                  const whole = members.every((id) => selectedNodeIds.includes(id));
                  if (event.ctrlKey || event.metaKey) {
                    event.preventDefault(); event.stopPropagation(); boardRef.current?.focus({ preventScroll: true });
                    setSelectedEdge(null);
                    if (!whole) setInspectorKey(`group:${group.id}`);
                    setSelectedNodeIds((current) => members.every((id) => current.includes(id)) ? current.filter((id) => !members.includes(id)) : [...new Set([...current, ...members])]);
                  } else {
                    startSelectionDrag(event, members, members, groupLocked(groups, group.id) ? group.name : "", `group:${group.id}`);
                  }
                }}
                onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); setSelectedNodeIds(members); setSelectedEdge(null); setInspectorKey(`group:${group.id}`); } }}
              >
                <div className="aw-group-title" style={{ transform: `scale(${titleScale})` }}><span>{group.icon ? <GroupIcon icon={group.icon} /> : null}<span className="aw-group-name">{group.name}</span>{groupLocked(groups, group.id) ? <span className="aw-lock-badge" role="img" aria-label="Locked"><Icons.Lock /></span> : null}</span></div>
              </div>
            ) : null)}
            <svg className="aw-wires" width={8000} height={8000}>
              {graph.edges.map((e, i) => {
                const a = nodesById.get(e.source);
                const b = nodesById.get(e.target);
                if (!a || !b) return null;
                if (e.kind === "data") {
                  const out = pinsOf(a).outputs.find((pin) => pin.id === e.source_pin);
                  if (!out || !pinsOf(b).inputs.some((pin) => pin.id === e.target_pin)) return null;  // a pin its settings removed
                  const from = pinPoint(a, layoutOf(a), "out", e.source_pin || "");
                  const to = pinPoint(b, layoutOf(b), "in", e.target_pin || "");
                  const d = wirePath(from.x, from.y, to.x, to.y);
                  const hot = selectedEdge === i || selectedNodeIds.includes(e.source) || selectedNodeIds.includes(e.target);
                  const flowing = !!live && (live.active === e.target || live.active === e.source);
                  const carried = !flowing && live?.states[e.source] === "ok" && live?.states[e.target] === "ok";
                  return (
                    <g key={`${e.source}-${e.source_pin}-${e.target}-${e.target_pin}`} data-aw-wire={`${e.source}.${e.source_pin}>${e.target}.${e.target_pin}`} className={`aw-edge aw-edge--data aw-pin-type--${cleanType(out.type)}${hot ? " is-hot" : ""}${flowing ? " is-live" : carried ? " is-passed" : ""}`}
                      onClick={(ev) => { ev.stopPropagation(); boardRef.current?.focus({ preventScroll: true }); setSelectedNodeIds([]); setSelectedEdge(i); setInspectorKey(`edge:${i}`); }}>
                      <path className="aw-edge-glow" d={d} />
                      <path className="aw-edge-hit" d={d} />
                      <path className="aw-wire aw-wire--data" d={d} />
                    </g>
                  );
                }
                const from = portPoint(a, "out", overview);
                const to = portPoint(b, "in", overview);
                const d = wirePath(from.x, from.y, to.x, to.y);
                const hot = selectedEdge === i || selectedNodeIds.includes(e.source) || selectedNodeIds.includes(e.target);
                const wire = `${e.source}>${e.target}`;
                const running = !!live && !!live.active && `${live.from}>${live.active}` === wire;
                return (
                  <g key={`${e.source}-${e.target}-${e.kind}-${i}`} data-aw-wire={`${e.source}>${e.target}:${e.kind || "main"}`} className={`aw-edge aw-edge--${e.kind} aw-edge--from-${nodeRole(a, byType.get(a.type))}${a.color ? ` aw-tint--${a.color}` : ""}${hot ? " is-hot" : ""}${running ? " is-live" : live?.passed.includes(wire) ? " is-passed" : ""}`}
                    onClick={(ev) => { ev.stopPropagation(); boardRef.current?.focus({ preventScroll: true }); setSelectedNodeIds([]); setSelectedEdge(i); setInspectorKey(`edge:${i}`); }}>
                    <path className="aw-edge-glow" d={d} />
                    <path className="aw-edge-hit" d={d} />
                    <path className={`aw-wire aw-wire--${e.kind}`} d={d} />
                    {/* A pulse that travels the wire from node to node, as in DuckyOS. */}
                    <path key={running ? "live" : "idle"} className="aw-edge-arrow" d="M -4 -3 L 4 0 L -4 3 Z">
                      <animateMotion dur={running ? "0.8s" : e.kind === "each" ? "2s" : "3.5s"} repeatCount="indefinite" rotate="auto" path={d} />
                    </path>
                  </g>
                );
              })}
              {draftWire ? (
                <g className={draftWire.pinType ? `aw-edge aw-edge--data aw-pin-type--${draftWire.pinType}` : undefined}>
                  <path
                    d={draftWire.dir === "out" ? wirePath(draftWire.fromX, draftWire.fromY, draftWire.toX, draftWire.toY) : wirePath(draftWire.toX, draftWire.toY, draftWire.fromX, draftWire.fromY)}
                    className={`aw-wire aw-wire--draft${draftWire.pinType ? " aw-wire--data" : ""}`}
                  />
                </g>
              ) : null}
            </svg>
            {graph.nodes.map((node) => {
              const meta = byType.get(node.type);
              const label = nodeLabel(node, meta);
              const summary = nodeSummary(node, meta);
              const calls = node.type === "workflow.call" ? String(node.config.workflow_id || "") : "";
              const locked = nodeLocked(graph, node.id);
              const pins = pinsOf(node);
              const layout = layoutOf(node);
              const shown = lastOutputs[node.id] || made[node.id] || {};
              const setHere = (node.config.inputs && typeof node.config.inputs === "object" ? node.config.inputs : {}) as Record<string, unknown>;
              const rowCount = overview ? 0 : layout.rows;
              return (
                <div
                  key={node.id}
                  ref={targetRef(`workflows.node.${node.id}`, { route: "workflows", label })}
                  data-aw-node={node.id}
                  onPointerDownCapture={(event) => toggleNodeSelection(event, node.id)}
                  onClickCapture={(event) => { if (event.ctrlKey || event.metaKey) { event.preventDefault(); event.stopPropagation(); } }}
                  className={`aw-node aw-node--${nodeRole(node, meta)}${pins.exec ? "" : " is-data"}${layout.hasPins ? " has-pins" : ""}${node.color ? ` aw-tint--${node.color}` : ""}${locked ? " is-locked" : ""}${selectedNodeIds.includes(node.id) ? " is-selected" : ""}${flashIds.includes(node.id) ? " is-flash" : ""}${live?.states[node.id] ? ` is-run-${live.states[node.id]}` : ""}${wireFrom === node.id ? " is-wiring" : ""}`}
                  style={{ left: node.x, top: node.y, ...(overview || !layout.hasPins ? {} : { height: layout.height }) }}
                >
                  {pins.exec && meta?.role !== "starter" ? <button
                    type="button"
                    aria-label={`Connect to ${label}`}
                    data-aw-port="in"
                    className={`aw-port aw-port--in${incomingIds.has(node.id) ? " is-linked" : ""}`}
                    data-aw-node={node.id}
                    onPointerDown={(e) => startWire(e, node, "in")}
                    onPointerMove={onBoardPointerMove}
                    onPointerUp={endPointer}
                    onPointerCancel={endPointer}
                  >{EXEC_PIN}</button> : null}
                  <div className="aw-node-card" onPointerDown={(e) => startNodeDrag(e, node)} title={calls ? `${label} — double-click to open the workflow it runs` : label}
                    onDoubleClick={() => { if (calls) void loadOne(calls); }}>
                    <div className="aw-node-title"><strong>{label}</strong>
                      {isCodeNode(node) ? <span className="aw-code-badge" role="img" aria-label="Custom code" title="Custom code: open its details, then Code">&lt;/&gt;</span> : null}
                      {locked ? <span className="aw-lock-badge" role="img" aria-label="Locked" title="Locked: unlock it in its details to move or change it"><Icons.Lock /></span> : null}</div>
                    <div className="aw-node-mark" aria-hidden="true"><NodeIcon meta={meta} node={node} faces={faces} basedMeta={byType.get(basedOnNode(node)?.type || "")} /></div>
                    {layout.hasPins && !overview ? (
                      <div className="aw-node-body aw-node-body--pins">
                        {pins.exec ? <div className="aw-node-exec-row">{summary ? <span className="aw-node-sub">{summary}</span> : null}</div> : null}
                        {Array.from({ length: rowCount }, (_, index) => {
                          const input = pins.inputs[index], output = pins.outputs[index];
                          const inValue = input && !fedPins.has(`${node.id}<${input.id}`) ? shortValue(setHere[input.id] ?? input.default) : "";
                          const outValue = output ? shortValue(shown[output.id] ?? (node.type.startsWith("input.") ? node.config.value : undefined)) : "";
                          return <div key={index} className="aw-pin-row">
                            <span className="aw-pin-label aw-pin-label--in">{input ? <>{input.label}{inValue ? <small>{inValue}</small> : null}</> : null}</span>
                            <span className="aw-pin-label aw-pin-label--out">{output ? <>{outValue ? <small>{outValue}</small> : null}{output.label}</> : null}</span>
                          </div>;
                        })}
                        {node.type === "util.preview"
                          ? picturesIn(shown.value).length ? <>
                            <Thumbs pictures={picturesIn(shown.value)} empty="" />
                            {readOnly || !draft?.id ? null : <PictureActions nodeId={node.id} running={runningNode} busy={busy} onRun={runNode} />}
                          </> : <div className="aw-node-preview">{previewText(shown.value)}</div>
                          : makesPictures(node, pins) ? <>
                            <Thumbs pictures={cardPictures(node, pins, shown)} empty={runningNode === node.id ? "Making it…" : "Press play to make one"} />
                            {readOnly || !draft?.id || !cardPictures(node, pins, shown).length ? null
                              : <PictureActions nodeId={node.id} running={runningNode} busy={busy} onRun={runNode} />}
                          </>
                          : cardExtra(node, pins) ? <Thumbs pictures={cardPictures(node, pins, shown)} empty="Pick a picture in the details" /> : null}
                      </div>
                    ) : (
                      <div className="aw-node-body">{!overview && summary ? <span className="aw-node-sub">{summary}</span> : null}</div>
                    )}
                    {!overview && live?.outputs[node.id] ? <TerminalOutput snapshot={live.outputs[node.id]} compact />
                      : !overview && live?.logs[node.id] ? <TerminalOutput snapshot={{ output: live.logs[node.id]!, sessionId: "" }} title="Log" compact /> : null}
                  </div>
                  {pins.inputs.map((pin) => <button key={`in-${pin.id}`} type="button" aria-label={`${label}: ${pin.label} (input)`} title={`${pin.label} · ${pin.type}`}
                    data-aw-pin="in" data-aw-pin-id={pin.id} data-aw-node={node.id}
                    className={`aw-pin aw-pin--in aw-pin-type--${cleanType(pin.type)}${fedPins.has(`${node.id}<${pin.id}`) ? " is-linked" : ""}`} style={{ top: layout.inputY[pin.id] }}
                    onPointerDown={(e) => startPinWire(e, node, "in", pin)} onPointerMove={onBoardPointerMove} onPointerUp={endPointer} onPointerCancel={endPointer} />)}
                  {pins.outputs.map((pin) => <button key={`out-${pin.id}`} type="button" aria-label={`${label}: ${pin.label} (output)`} title={`${pin.label} · ${pin.type}`}
                    data-aw-pin="out" data-aw-pin-id={pin.id} data-aw-node={node.id}
                    className={`aw-pin aw-pin--out aw-pin-type--${cleanType(pin.type)}${fedPins.has(`${node.id}>${pin.id}`) ? " is-linked" : ""}`} style={{ top: layout.outputY[pin.id] }}
                    onPointerDown={(e) => startPinWire(e, node, "out", pin)} onPointerMove={onBoardPointerMove} onPointerUp={endPointer} onPointerCancel={endPointer} />)}
                  {pins.exec && !isEndNode(node) && <button
                    type="button"
                    aria-label={`Connect from ${label}`}
                    data-aw-port="out"
                    className={`aw-port aw-port--out${outgoingIds.has(node.id) ? " is-linked" : ""}`}
                    data-aw-node={node.id}
                    onPointerDown={(e) => startWire(e, node, "out")}
                    onPointerMove={onBoardPointerMove}
                    onPointerUp={endPointer}
                    onPointerCancel={endPointer}
                  >{EXEC_PIN}</button>}
                </div>
              );
            })}
            {marquee ? <div className="aw-selection-box" aria-hidden="true" style={{ left: marquee.x, top: marquee.y, width: marquee.width, height: marquee.height }} /> : null}
          </div>
        </div>
        {textSaveError ? <p className="aw-text-save-error" role="alert">{textSaveError}</p> : null}
        {actionError ? <p className="aw-text-save-error" role="alert">{actionError}</p> : null}
        {draft && readOnly ? <p className="aw-readonly-note" role="note"><Icons.Lock /> {draft.owner?.reason || "Read-only here."} Duplicate it to change a Local copy.</p> : null}
        {panelBadge ? createPortal(<div className="aw-panel-zoom" aria-live="polite" style={{ left: panelBadge.left, top: panelBadge.top }}>{Math.round(panelBadge.value * 100)}%</div>, document.body) : null}
        {notice ? <p key={notice.id} className={`aw-toast aw-toast--${notice.kind}`} role="status">{notice.kind === "note" ? <Icons.Sparkles /> : <Icons.Lock />} {notice.text}</p> : null}
        <button type="button" className="aw-canvas-fab" aria-label="Canvas tools" title="Canvas tools" aria-expanded={phoneMenu === "canvas"}
          onClick={() => setPhoneMenu((open) => (open === "canvas" ? "" : "canvas"))}>
          {phoneMenu === "canvas" ? <Icons.Close /> : <Icons.Grid />}
        </button>
        <div className={`aw-canvas-controls${phoneMenu === "canvas" ? " is-open" : ""}`} role="toolbar" aria-label="Canvas" data-aw-zoom="controls" onClick={closePhoneMenuAfterPick}>
          <button type="button" ref={targetRef("workflows.tool.select", { route: "workflows", label: "Select tool" })} className="aw-tool" aria-label="Select tool" aria-pressed={activeTool === "select"} title="Select (V): drag on empty canvas to select a box of nodes" onClick={() => setTool("select")}><Icons.Cursor /></button>
          <button type="button" ref={targetRef("workflows.tool.hand", { route: "workflows", label: "Hand tool" })} className="aw-tool" aria-label="Hand tool" aria-pressed={activeTool === "hand"} title="Hand (H, or hold Space): drag the canvas to move around" onClick={() => setTool("hand")}><Icons.Hand /></button>
          <span className="aw-controls-sep" aria-hidden="true" />
          <button type="button" ref={targetRef("workflows.log", { route: "workflows", label: "Run log" })} className={`aw-log-toggle${logOpen ? " is-open" : ""}`} title={logOpen ? "Hide run log" : "Run log"} aria-label="Run log" aria-expanded={logOpen} onClick={() => setLogOpen((v) => !v)}>
            <Icons.Sliders />
            {busy || runningNode ? <span className="aw-log-badge is-live" aria-label="Running"><Icons.Spinner /></span> : logCount ? <span className="aw-log-badge">{logCount}</span> : null}
          </button>
          <button type="button" ref={targetRef("workflows.add", { route: "workflows", label: "Add nodes" })} title="Add nodes (or right-click the canvas)" aria-label="Add nodes" onClick={(event) => openAddMenu(event.currentTarget)} disabled={!draft}><Icons.Plus /></button>
          <span className="aw-controls-sep" aria-hidden="true" />
          <button type="button" ref={targetRef("workflows.fit", { route: "workflows", label: "Fit view" })} title={selectedNodeIds.length === 1 ? "Zoom to the selected node (F)" : selectedNodeIds.length ? "Fit the selection (F)" : "Fit the whole workflow (F)"} aria-label="Fit view" onClick={() => fitView()} disabled={!draft}><Icons.FitView /></button>
          <CanvasMenu label="Zoom" title="Zoom, grid and snapping" className="aw-zoom" buttonRef={targetRef("workflows.zoom", { route: "workflows", label: "Zoom and grid" })}
            trigger={<span className="aw-zoom-label">{Math.round(zoom * 100)}%</span>}
            onPick={(value) => { if (value === "snap") setSnap((on) => !on); else if (value.startsWith("grid:")) setGridStyle(value.slice(5) as GridStyle); else zoomTo(value); }}
            items={[
              { value: "in", label: "Zoom in" },
              { value: "out", label: "Zoom out" },
              { value: "fit", label: "Zoom to fit", hint: "F" },
              ...ZOOM_STEPS.map((step, index) => ({ value: String(step), label: `${step * 100}%`, checked: Math.abs(zoom - step) < 0.005, separatorBefore: index === 0 })),
              ...GRID_STYLES.map((item, index) => ({ value: `grid:${item.value}`, label: item.label, checked: item.value === gridStyle, separatorBefore: index === 0, heading: index === 0 ? "Grid" : undefined })),
              { value: "snap", label: "Snap to grid", toggle: true, checked: snap },
            ]} />
          <WorkflowOutline graph={graph} byType={byType} faces={faces} selectedIds={selectedNodeIds} buttonRef={targetRef("workflows.outline", { route: "workflows", label: "Outline" })}
            onPick={(ids, key) => { setSelectedEdge(null); setSelectedNodeIds(ids); setInspectorKey(key); fitView(ids, true); }} />
          <CanvasMenu label="Help" title="Tours: learn Workflows, build your first one, or walk through this workflow" className="aw-help"
            buttonRef={targetRef("workflows.help", { route: "workflows", label: "Help and tours" })}
            trigger={<Icons.Help />}
            onPick={(value) => void startHelp(value)}
            items={[
              { value: WORKFLOWS_INTRO_TOUR_ID, label: "Tour of Workflows" },
              { value: WORKFLOWS_FIRST_BUILD_TOUR_ID, label: "Build your first workflow" },
              { value: WORKFLOWS_EDITOR_TOUR_ID, label: "Editor basics" },
              ...(draft?.graph.nodes.length ? [{ value: "this", label: "Tour this workflow", separatorBefore: true }] : []),
            ]} />
        </div>
        {draft ? <WorkflowInspector
          tabs={inspectorTabs}
          activeKey={inspectorKey}
          graph={graph}
          byType={byType}
          faces={faces}
          readOnly={readOnly}
          workflows={rows}
          currentId={draft.id}
          onActivate={setInspectorKey}
          onClose={() => { setSelectedNodeIds([]); setSelectedEdge(null); }}
          onOpenWorkflow={(id) => void loadOne(id)}
          onNodeChange={changeNode}
          onNodeReplace={replaceNode}
          onCodeSession={codeTyping}
          team={teamWorkflow}
          detailsTab={detailsTab}
          onDetailsTab={pickDetailsTab}
          codeWide={codeWide}
          onCodeWide={setCodeWide}
          codeFocus={codeFocus}
          savedAt={draft.updated}
          lastSteps={lastSteps}
          pinsOf={pinsOf}
          nodeOutputs={lastOutputs}
          liveNodes={liveNodes}
          onRunNode={readOnly ? undefined : (id) => void runNode(id)}
          runningNode={runningNode}
          onNodeText={updateNodeText}
          onNodeColor={setNodeColor}
          onNodeIcon={setNodeIcon}
          onDeleteNodes={(ids) => void confirmDelete(ids.length === 1 ? `Delete “${nameOf(ids[0])}”?` : `Delete ${ids.filter((id) => !nodeLocked(graph, id)).length} nodes?`, "Delete", () => removeNodes(ids))}
          onTakeOut={(id) => nodeLocked(graph, id) ? notify(lockedNote([id], "taken out of a group")) : saveGraphChange((current) => ungroupNodes(current, [id]))}
          onLock={setLocked}
          onSelectNodes={(ids) => { setSelectedEdge(null); setSelectedNodeIds(ids); setInspectorKey(ids.length === 1 ? `node:${ids[0]}` : ""); }}
          onGroupChange={(id, patch) => !groupLocked(groups, id) && saveGraphChange((current) => ({ ...current, groups: current.groups?.map((item) => {
            if (item.id !== id) return item;
            const { color, icon, ...rest } = { ...item, ...patch };
            return { ...rest, ...(color ? { color } : {}), ...(icon ? { icon } : {}) };
          }) }))}
          onUngroup={(id) => groupLocked(groups, id) ? notify("This group is locked. Unlock it to ungroup it.") : saveGraphChange((current) => removeGroup(current, id))}
          onMakeReusable={(id) => void makeReusable(id)}
          onDeleteGroup={(id) => {
            const members = groupMembers(graph.groups || [], id);
            const name = graph.groups?.find((group) => group.id === id)?.name || "this group";
            void confirmDelete(`Delete ${name} and its ${members.length} node${members.length === 1 ? "" : "s"}?`, "Delete", () => removeNodes(members));
          }}
          onEdgeRoute={(index, kind) => lockedEdge(graph.edges[index]) ? notify(lockedNote([lockedEdge(graph.edges[index])!], "rewired")) : patchGraph((g) => ({ ...g, edges: g.edges.map((edge, i) => (i === index ? { ...edge, kind } : edge)) }))}
          onDisconnect={(index) => void confirmDelete("Remove this connection?", "Remove", () => disconnect(index))}
          onGroupSelection={selectionGroupable ? groupSelection : undefined}
        /> : null}
        {draft && inspectorTabs.length && !codeWideOn ? <PanelResizeHandle label="Resize details panel" className="aw-resize--inspector" value={panelWidths.inspector} min={INSPECTOR_W.min} max={INSPECTOR_W.max} edge="left"
          onResize={(inspector) => setPanelWidths((current) => ({ ...current, inspector }))} onActive={setResizingPanel} /> : null}
        {/* Always mounted so it can grow out of (and shrink back into) the Run log button. */}
        <div
            data-aw-zoom="log"
            className={`aw-log-dock${logOpen ? " is-open" : ""}`}
            aria-hidden={!logOpen}
            style={{ height: logHeight }}
            onPointerDown={(e) => e.stopPropagation()}
            onPointerMove={(e) => e.stopPropagation()}
            onPointerUp={(e) => e.stopPropagation()}
            onWheel={(e) => e.stopPropagation()}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const picked = window.getSelection()?.toString().trim();
              copyLog(picked || undefined);
            }}
          >
            <div
              className="aw-log-resize"
              title="Drag to resize"
              onPointerDown={onLogResizeDown}
              onPointerMove={onLogResizeMove}
              onPointerUp={onLogResizeUp}
              onPointerCancel={onLogResizeUp}
            />
            <div className="aw-log-dock-head">
              <strong>Run log</strong>
              <button
                type="button"
                className="aw-log-copy"
                title="Copy log"
                disabled={!runLogHasContent(log)}
                onClick={() => copyLog()}
              >
                {logCopied ? "Copied" : "Copy log"}
              </button>
              <button type="button" className="aw-log-copy aw-log-clear" title="Clear this PC's run log for this workflow" disabled={!runLogHasContent(log)} onClick={() => void clearLog()}>
                Clear log
              </button>
              <button type="button" className="icon-btn" title="Hide" aria-label="Hide run log" onClick={() => setLogOpen(false)}>
                <Icons.Close />
              </button>
            </div>
            <div className="aw-log-dock-body selectable-text">
              {/* Live while anything runs: a test, one node, Try again or Use this. */}
              {busy || runningNode ? (
                <ol className="aw-live-steps" aria-live="polite">
                  {live?.lines.length ? live.lines.map((line, index) => <li key={index} className={`aw-live-line is-${line.state}${line.state === "error" ? " is-err" : ""}`}>
                    {line.state === "running" ? <span className="aw-live-spin" aria-hidden="true"><Icons.Spinner /></span> : null}
                    {line.label} {line.state === "running" ? "· running…" : line.state === "ok" ? "ok" : line.state === "stopped" ? "· stopped" : `— ${line.error || "failed"}`}
                  </li>) : <li className="aw-live-line is-running"><span className="aw-live-spin" aria-hidden="true"><Icons.Spinner /></span>Starting…</li>}
                </ol>
              ) : runLogHasContent(log) ? (<>
                <ol>
                  {log?.ok === false && log.error ? <li className="is-err">{log.error}</li> : null}
                  <RunSteps steps={log?.steps || []} onOpenCode={openCodeAt} />
                </ol>
                {log?.outputs && Object.keys(log.outputs).length ? <p className="aw-log-returned">Returned: {Object.entries(log.outputs).map(([key, value]) => `${key} = ${typeof value === "string" ? value : JSON.stringify(value)}`).join(", ")}</p> : null}
              </>) : (
                <p>
                  Test a workflow to see steps here. Return to user sends results back to your chat. Schedules only fire while the panel is running.
                </p>
              )}
            </div>
          </div>
      </div>
      {spawn ? (
        <div className="aw-spawn-scrim" onMouseDown={() => setSpawn(null)}>
          <div
            className={`aw-spawn-menu${spawn.bottom !== undefined ? " is-from-below" : ""}`}
            role="dialog"
            aria-label="Add node"
            style={{
              left: spawn.x,
              top: spawn.bottom === undefined ? spawn.y : undefined,
              bottom: spawn.bottom,
              maxHeight: `min(512px, calc(100dvh - ${(spawn.bottom ?? spawn.y ?? 0) + 8}px))`,
              "--aw-spawn-origin": spawn.origin || "0 0",
            } as React.CSSProperties}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <label className="aw-spawn-searchbar">
              <Icons.Search />
              <input
                ref={spawnSearchRef}
                className="aw-spawn-search"
                aria-label="Filter nodes"
                placeholder="Search nodes"
                spellCheck={false}
                value={spawnFilter}
                onChange={(e) => setSpawnFilter(e.target.value)}
              />
            </label>
            <div className="aw-spawn-section"><span>{spawn.wire ? "Add a connected node" : "Add a node"}</span><button type="button" onClick={() => setGroupsCollapsed((v) => !v)}>{groupsCollapsed ? "Expand groups" : "Collapse groups"}</button></div>
            <div className="aw-spawn-scroll">
              {spawnGroups.length ? (
                spawnGroups.map(([name, tiles]) => (
                  <details key={`${name}-${groupsCollapsed}-${!!spawnFilter}`} className="aw-acc" open={!groupsCollapsed || !!spawnFilter}>
                    <summary><NodeIcon meta={tiles[0]} /><span className="aw-acc-name">{name}</span><span className="aw-acc-count" title={`${tiles.length} node${tiles.length === 1 ? "" : "s"}`}>{tiles.length}</span></summary>
                    {tiles.map((t) => (
                      <button
                        key={t.type + (t.workflowId || "")}
                        ref={targetRef(`workflows.palette.${t.type}${t.workflowId ? `.${t.workflowId}` : ""}`, { route: "workflows", label: t.label })}
                        type="button"
                        className="aw-tile"
                        onClick={() => addNodeAt(t, spawn.worldX, spawn.worldY)}
                      >
                        <span className="aw-tile-head"><NodeIcon meta={t} /><strong>{t.label}</strong></span>
                        {t.description ? <small>{t.description}</small> : null}
                      </button>
                    ))}
                  </details>
                ))
              ) : !catalog.length && catalogState === "loading" ? (
                <p className="aw-empty-hint">Loading nodes…</p>
              ) : !catalog.length ? (
                <p className="aw-empty-hint">Could not load the node list. <button type="button" className="aw-link" onClick={() => void loadCatalog()}>Try again</button></p>
              ) : (
                <p className="aw-empty-hint">No matching nodes.</p>
              )}
            </div>
          </div>
        </div>
      ) : null}
      <AutomationTemplatePicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={(t) => void createFromTemplate(t)}
        currentGraph={draft?.graph || null}
        ownerLabel={ownerName((owners.owners || []).find((owner) => owner.id === pickerOwner) || LOCAL_OWNER)}
        owners={owners.owners || [LOCAL_OWNER]}
        ownerId={pickerOwner}
        onOwnerChange={setPickerOwner}
      />
    </div>
  );
}
