import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { Icons } from "../icons/Icons";
import type {
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphGroupDto,
  AutomationGraphNodeDto,
  AutomationNodeDto,
  AutomationRunStepDto,
  AutomationSummaryDto,
} from "../types/panel";
import { NodeSettings, hasNodeSettings } from "./NodeSettings";
import { basedOnNode, GroupIcon, NodeIcon, nodeLabel, nodeRole } from "./NodeVisuals";
import { CodeTab } from "./CodeTab";
import { IconPicker } from "./IconPicker";
import { PinsSection } from "./PinFields";
import { LiveNodeStatus, type LiveNodeRun } from "./LiveNodeStatus";
import type { NodePins } from "./pins";
import { GROUP_COLORS, groupLocked, groupMembers, nodeLocked } from "./workflowGroups";
import { targetRef } from "../ui-targets/registry";

export type InspectorTab =
  | { key: string; kind: "node"; node: AutomationGraphNodeDto }
  | { key: string; kind: "group"; group: AutomationGraphGroupDto }
  | { key: string; kind: "edge"; index: number; edge: AutomationGraphEdgeDto };

export const EDGE_ROUTES = [{ value: "main", label: "Next" }, { value: "true", label: "True" }, { value: "false", label: "False" }, { value: "each", label: "Each item" }, { value: "done", label: "Done" }];
/** A Repeat until loops tries, not items. */
const REPEAT_ROUTES = EDGE_ROUTES.map((route) => (route.value === "each" ? { ...route, label: "Each try" } : route));

export type InspectorActions = {
  onActivate: (key: string) => void;
  onClose: () => void;
  onOpenWorkflow: (id: string) => void;
  /** Settings edits go into the draft; Save keeps them. `label` names the undo step. */
  onNodeChange: (node: AutomationGraphNodeDto, label?: string) => void;
  /** Edit as custom code / Revert to built-in: the node changes type in the draft and is saved. */
  onNodeReplace?: (node: AutomationGraphNodeDto, label: string) => void;
  /** Typing in a node's code begins and ends one undo step. */
  onCodeSession?: (phase: "begin" | "end", nodeId: string) => void;
  /** Settings or Code under a node's name (kept on this PC). */
  onDetailsTab?: (tab: DetailsTab) => void;
  /** Pop out: the details panel widens while the Code tab is open. */
  onCodeWide?: (wide: boolean) => void;
  /** Name and description save as soon as they are committed. */
  onNodeText: (id: string, patch: { label?: string; description?: string }) => void;
  /** "" goes back to the color of its kind. */
  onNodeColor: (id: string, color: string) => void;
  /** An emoji of its own; "" goes back to the icon of its kind. */
  onNodeIcon: (id: string, icon: string) => void;
  onDeleteNodes: (ids: string[]) => void;
  onTakeOut: (id: string) => void;
  onSelectNodes: (ids: string[]) => void;
  onGroupChange: (id: string, patch: { name?: string; color?: string; icon?: string }) => void;
  onUngroup: (id: string) => void;
  onMakeReusable: (id: string) => void;
  onDeleteGroup: (id: string) => void;
  onEdgeRoute: (index: number, kind: string) => void;
  onDisconnect: (index: number) => void;
  /** Present when the selection can become one group. */
  onGroupSelection?: () => void;
  /** Lock or unlock nodes and groups: locked ones can't be moved, wired or changed. */
  onLock: (targets: { nodes?: string[]; groups?: string[] }, locked: boolean) => void;
  /** Run this node only (what feeds it reuses the last run). */
  onRunNode?: (id: string) => void;
  /** The node running on its own now, if any. */
  runningNode?: string;
};

type Props = InspectorActions & {
  tabs: InspectorTab[];
  activeKey: string;
  graph: AutomationGraphDto;
  byType: Map<string, AutomationNodeDto>;
  faces: Record<string, string>;
  readOnly: boolean;
  workflows: AutomationSummaryDto[];
  currentId?: string;
  /** Each node's typed pins (from the canvas). */
  pinsOf?: (node: AutomationGraphNodeDto) => NodePins;
  /** The last run's values per node and output pin. */
  nodeOutputs?: Record<string, Record<string, unknown>>;
  /** The run going on now (or just ended): each step's state, time, error and terminal log. */
  liveNodes?: Record<string, LiveNodeRun>;
  /** Each node's newest step in the last run (inputs, log, code error). */
  lastSteps?: Record<string, AutomationRunStepDto>;
  /** A team's workflow (custom code runs in Local ones only for now). */
  team?: boolean;
  detailsTab?: DetailsTab;
  codeWide?: boolean;
  /** Open this node's Code tab at a line (a failed code step clicked in the run log). */
  codeFocus?: { nodeId: string; line: number; nonce: number } | null;
};

export type DetailsTab = "settings" | "code";
export const DETAILS_TAB_KEY = "ducky.workflows.detailsTab";

export function readDetailsTab(): DetailsTab {
  try { return window.localStorage.getItem(DETAILS_TAB_KEY) === "code" ? "code" : "settings"; } catch { return "settings"; }
}

export function writeDetailsTab(tab: DetailsTab) {
  try { window.localStorage.setItem(DETAILS_TAB_KEY, tab); } catch { /* private mode */ }
}

const COLOR_NAMES: Record<string, string> = { "": "Plain", red: "Red", amber: "Gold", green: "Green", blue: "Blue", purple: "Purple" };

/** The top of the details: the name and description written out, the icon on the right.
 *  The pen in the header (or a double-click on the text) makes the text itself editable
 *  (no boxes); Save keeps it, Cancel or Esc puts it back. Enter in the name or Ctrl+Enter
 *  anywhere saves too. */
function DetailsHead({ icon, kind, name, nameLabel, description, fallback, editable, editing = false, onEditing, onSave }: {
  /** The icon at the top right: an IconPicker while it can change. */
  icon: React.ReactNode;
  kind: string;
  name: string;
  nameLabel: string;
  /** Undefined: it has no description (groups, connections). */
  description?: string;
  /** Shown, muted, while it has no description of its own. */
  fallback?: string;
  editable: boolean;
  editing?: boolean;
  onEditing?: (editing: boolean) => void;
  onSave?: (patch: { name?: string; description?: string }) => void;
}) {
  const setEditing = (value: boolean) => onEditing?.(value);
  const [error, setError] = useState("");
  const nameRef = useRef<HTMLHeadingElement>(null);
  const descRef = useRef<HTMLDivElement>(null);
  const hasDescription = description !== undefined;
  useEffect(() => { if (!editable && editing) setEditing(false); }, [editable]);
  useEffect(() => { if (!editing) setError(""); }, [editing]);
  // Fill the text once when editing starts (typing must not be overwritten), then select the name.
  useEffect(() => {
    if (!editing) return;
    if (nameRef.current) nameRef.current.textContent = name;
    if (descRef.current) descRef.current.textContent = description ?? "";
    const el = nameRef.current;
    el?.focus({ preventScroll: true });
    const selection = window.getSelection?.();
    if (el && selection && typeof document.createRange === "function") {
      const range = document.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }, [editing]);
  const cancel = () => { setEditing(false); setError(""); };
  const save = () => {
    const nextName = (nameRef.current?.textContent || "").replace(/\s+/g, " ").trim();
    const nextDescription = (descRef.current?.textContent || "").trim();
    if (!nextName) { setError("It needs a name."); nameRef.current?.focus({ preventScroll: true }); return; }
    const patch: { name?: string; description?: string } = {};
    if (nextName !== name) patch.name = nextName;
    if (hasDescription && nextDescription !== (description || "")) patch.description = nextDescription;
    setEditing(false);
    setError("");
    if (patch.name !== undefined || patch.description !== undefined) onSave?.(patch);
  };
  const keys = (multiline: boolean) => (event: React.KeyboardEvent) => {
    event.stopPropagation();
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape") { event.preventDefault(); cancel(); }
    else if (event.key === "Enter" && (event.ctrlKey || event.metaKey || !multiline)) { event.preventDefault(); save(); }
  };
  const shown = description || fallback || "";
  return <div className={`aw-insp-top${editing ? " is-editing" : ""}`}>
    <div className="aw-insp-top-text">
      <span className="aw-insp-kind">{kind}</span>
      {editing ? <>
        <h3 key="edit-name" ref={nameRef} className="aw-insp-name" contentEditable="plaintext-only" suppressContentEditableWarning role="textbox" aria-label={nameLabel} spellCheck={false} onKeyDown={keys(false)} />
        {hasDescription ? <div key="edit-description" ref={descRef} className="aw-insp-desc" contentEditable="plaintext-only" suppressContentEditableWarning role="textbox" aria-multiline="true" aria-label="Description"
          data-placeholder={fallback || "What this step is for"} spellCheck={false} onKeyDown={keys(true)} /> : null}
      </> : <>
        <h3 key="name" className="aw-insp-name" onDoubleClick={() => editable && setEditing(true)}>{name}</h3>
        {hasDescription && shown ? <p key="description" className={`aw-insp-desc${description ? "" : " is-default"}`} onDoubleClick={() => editable && setEditing(true)}>{shown}</p> : null}
      </>}
    </div>
    {icon}
    {error ? <p className="aw-field-error aw-insp-top-error" role="alert">{error}</p> : null}
    {editable && editing ? <div className="aw-insp-top-actions">
      <button type="button" className="is-primary" onClick={save}><Icons.Check /> Save</button>
      <button type="button" onClick={cancel}>Cancel</button>
    </div> : null}
  </div>;
}

/** Swatches: the plain one (by kind for a node, the plain frame for a group) and the five colors. */
function ColorField({ label, value, plainLabel, className, onChange }: { label: string; value: string; plainLabel: string; className: string; onChange: (color: string) => void }) {
  const id = useId();
  return <div className="aw-field">
    <span className="aw-field-label" id={id}>{label}</span>
    <div className={`aw-swatches ${className}`} role="radiogroup" aria-labelledby={id}>
      {GROUP_COLORS.map((color) => {
        const name = color ? COLOR_NAMES[color] : plainLabel;
        return <button key={color} type="button" role="radio" aria-checked={value === color} aria-label={name} title={name}
          className={`aw-swatch${color ? ` aw-tint--${color}` : ""}`} onClick={() => onChange(color)} />;
      })}
    </div>
  </div>;
}

/** The nearest locked group around `groupId` (itself included), if any. */
function lockingGroup(groups: AutomationGraphGroupDto[], groupId: string | undefined): AutomationGraphGroupDto | undefined {
  const seen = new Set<string>();
  for (let at = groupId; at && !seen.has(at); at = groups.find((group) => group.id === at)?.parent_id) {
    seen.add(at);
    const group = groups.find((item) => item.id === at);
    if (group?.locked) return group;
  }
  return undefined;
}

function tabLabel(tab: InspectorTab, byType: Map<string, AutomationNodeDto>, graph: AutomationGraphDto) {
  if (tab.kind === "group") return tab.group.name;
  if (tab.kind === "node") return nodeLabel(tab.node, byType.get(tab.node.type));
  const name = (id: string) => { const node = graph.nodes.find((item) => item.id === id); return node ? nodeLabel(node, byType.get(node.type)) : id; };
  return `${name(tab.edge.source)} → ${name(tab.edge.target)}`;
}

function TabIcon({ tab, byType, faces }: { tab: InspectorTab; byType: Map<string, AutomationNodeDto>; faces: Record<string, string> }) {
  if (tab.kind === "node") return <NodeIcon meta={byType.get(tab.node.type)} node={tab.node} faces={faces} basedMeta={byType.get(basedOnNode(tab.node)?.type || "")} />;
  if (tab.kind === "group") return <GroupIcon icon={tab.group.icon} />;
  return <span className="aw-node-icon" aria-hidden><Icons.GitBranch /></span>;
}

/** The details panel that slides in from the right: one tab per selected node or group. */
export function WorkflowInspector(props: Props) {
  const { tabs, activeKey, graph, byType, faces, readOnly } = props;
  const open = tabs.length > 0;
  // Keep showing the last selection while the panel slides away.
  const last = useRef(tabs);
  if (open) last.current = tabs;
  const shown = open ? tabs : last.current;
  const active = shown.find((tab) => tab.key === activeKey) || shown[0];
  const panelRef = useRef<HTMLElement | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  useEffect(() => { if (panelRef.current) panelRef.current.inert = !open; }, [open]);
  // Bring the active tab into view by scrolling the strip only: scrollIntoView would also
  // scroll the page while the panel is still sliding in from off-screen.
  useEffect(() => {
    const strip = tabsRef.current;
    const tab = strip?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!strip || !tab) return;
    if (tab.offsetLeft < strip.scrollLeft) strip.scrollLeft = tab.offsetLeft - 8;
    else if (tab.offsetLeft + tab.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = tab.offsetLeft + tab.offsetWidth - strip.clientWidth + 8;
  }, [active?.key]);
  const nodeIds = shown.filter((tab) => tab.kind === "node").map((tab) => tab.kind === "node" ? tab.node.id : "");
  const groupIds = shown.filter((tab) => tab.kind === "group").map((tab) => tab.kind === "group" ? tab.group.id : "");
  const deletable = nodeIds.filter((id) => !nodeLocked(graph, id));
  const allLocked = nodeIds.every((id) => nodeLocked(graph, id)) && groupIds.every((id) => groupLocked(graph.groups || [], id));
  const head = headActions(shown, active, graph, byType, nodeIds, groupIds, deletable, allLocked, readOnly, props);
  // The pen edits the open tab: its name, description, color and icon.
  const [editingKey, setEditingKey] = useState("");
  const editable = !!active && canEdit(active, graph, readOnly);
  const editing = !!active && editingKey === active.key && editable;
  useEffect(() => { if (editingKey && (!open || editingKey !== active?.key)) setEditingKey(""); }, [open, active?.key]);
  const editProps = { editing, onEditing: (value: boolean) => setEditingKey(value && active ? active.key : "") };

  const onTabKey = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const at = shown.findIndex((tab) => tab.key === active?.key);
    const next = shown[(at + (event.key === "ArrowRight" ? 1 : -1) + shown.length) % shown.length];
    props.onActivate(next.key);
    [...(tabsRef.current?.querySelectorAll<HTMLElement>('[role="tab"]') || [])].find((el) => el.dataset.tabKey === next.key)?.focus({ preventScroll: true });
  };

  return <aside ref={(el) => { panelRef.current = el; targetRef("workflows.details", { route: "workflows", label: "Details panel" })(el); }} className={`aw-inspector${open ? " is-open" : ""}${props.codeWide && active?.kind === "node" && props.detailsTab === "code" ? " is-wide" : ""}`} aria-label="Details" data-aw-zoom="details" aria-hidden={!open}>
    <header className="aw-insp-head">
      <strong>{shown.length > 1 ? `${shown.length} selected` : active?.kind === "group" ? "Group" : active?.kind === "edge" ? "Connection" : "Node"}</strong>
      {head.lockedNote ? <span className="aw-insp-state" title={head.lockedNote}><Icons.Lock /> Locked</span> : null}
      <div className="aw-insp-head-actions">
        {props.onGroupSelection ? <button type="button" className="aw-icon-button" aria-label="Group selection" title="Put the selection in a group (Ctrl+G)" onClick={props.onGroupSelection}><Icons.Box /></button> : null}
        {shown.length === 1 && active?.kind === "node" && props.onRunNode ? <button type="button" ref={targetRef("workflows.details.run", { route: "workflows", label: "Run this node only" })}
          className={`aw-icon-button aw-run-node${props.runningNode === active.node.id ? " is-running" : ""}`} aria-label="Run this node"
          disabled={!!props.runningNode} title="Run only this node now. What feeds it reuses the last run, so paid steps before it don't run again. Pressing it allows the paid steps this run needs."
          onClick={() => props.onRunNode?.(active.node.id)}>{props.runningNode === active.node.id ? <Icons.Spinner /> : <Icons.Play />}</button> : null}
        {editable ? <button type="button" ref={targetRef("workflows.details.edit", { route: "workflows", label: "Edit name, description, color and icon" })} className={`aw-icon-button aw-edit-toggle${editing ? " is-on" : ""}`}
          aria-label="Edit" aria-pressed={editing} title={editing ? "Stop editing (Esc)" : "Edit the name, description, color and icon"} onClick={() => editProps.onEditing(!editing)}>
          <Icons.Pencil />
        </button> : null}
        {head.lock ? <button type="button" ref={targetRef("workflows.details.lock", { route: "workflows", label: "Lock or unlock" })} className={`aw-icon-button aw-lock-toggle${head.lock.on ? " is-on" : ""}`}
          aria-label={head.lock.label} aria-pressed={head.lock.on} disabled={head.lock.disabled} title={head.lock.title} onClick={head.lock.run}>
          {head.lock.on ? <Icons.Lock /> : <Icons.Unlock />}
        </button> : null}
        {head.remove ? <button type="button" ref={targetRef("workflows.details.delete", { route: "workflows", label: "Delete" })} className="aw-icon-button is-danger"
          aria-label={head.remove.label} disabled={head.remove.disabled} title={head.remove.title} onClick={head.remove.run}><Icons.Trash /></button> : null}
        <button type="button" ref={targetRef("workflows.details.close", { route: "workflows", label: "Close details" })} className="aw-icon-button" aria-label="Close details" title="Close (Esc)" onClick={props.onClose}><Icons.Close /></button>
      </div>
    </header>
    {shown.length > 1 ? <div ref={tabsRef} className="aw-insp-tabs" role="tablist" aria-label="Selected items" onKeyDown={onTabKey}>
      {shown.map((tab) => {
        const selected = tab.key === active?.key;
        return <button key={tab.key} type="button" role="tab" data-tab-key={tab.key} id={`${baseId}-${tab.key}`} aria-selected={selected} aria-controls={`${baseId}-panel`} tabIndex={selected ? 0 : -1}
          className={`aw-insp-tab aw-insp-tab--${tab.kind === "node" ? nodeRole(tab.node, byType.get(tab.node.type)) : tab.kind}${tab.kind === "node" && tab.node.color ? ` aw-tint--${tab.node.color}` : ""}`} onClick={() => props.onActivate(tab.key)}>
          <TabIcon tab={tab} byType={byType} faces={faces} />
          <span>{tabLabel(tab, byType, graph)}</span>
          {(tab.kind === "node" && nodeLocked(graph, tab.node.id)) || (tab.kind === "group" && groupLocked(graph.groups || [], tab.group.id)) ? <span className="aw-tab-lock" role="img" aria-label="Locked"><Icons.Lock /></span> : null}
        </button>;
      })}
    </div> : null}
    <div className="aw-insp-body" id={`${baseId}-panel`} role={shown.length > 1 ? "tabpanel" : undefined} aria-labelledby={shown.length > 1 && active ? `${baseId}-${active.key}` : undefined}>
      {active?.kind === "node" ? <NodeDetails key={active.key} {...props} {...editProps} node={active.node} />
        : active?.kind === "group" ? <GroupDetails key={active.key} {...props} {...editProps} group={active.group} />
        : active?.kind === "edge" ? <EdgeDetails key={active.key} {...props} index={active.index} edge={active.edge} /> : null}
    </div>
  </aside>;
}


type HeadButton = { label: string; title: string; disabled: boolean; run: () => void };
type EditProps = { editing: boolean; onEditing: (editing: boolean) => void };

/** Whether the pen shows: a node or group nothing locks (connections have no name of their own). */
function canEdit(tab: InspectorTab, graph: AutomationGraphDto, readOnly: boolean): boolean {
  if (readOnly || tab.kind === "edge") return false;
  const groups = graph.groups || [];
  if (tab.kind === "group") return !groupLocked(groups, tab.group.id);
  return !nodeLocked(graph, tab.node.id);
}

/** What the header's lock and delete icons do for this selection. */
function headActions(shown: InspectorTab[], active: InspectorTab | undefined, graph: AutomationGraphDto, byType: Map<string, AutomationNodeDto>, nodeIds: string[], groupIds: string[], deletable: string[], allLocked: boolean, readOnly: boolean, on: InspectorActions) {
  const groups = graph.groups || [];
  const name = (id: string) => { const node = graph.nodes.find((item) => item.id === id); return node ? nodeLabel(node, byType.get(node.type)) : id; };
  let lock: (HeadButton & { on: boolean }) | null = null;
  let remove: HeadButton | null = null;
  let lockedNote = "";
  if (shown.length > 1) {
    if (nodeIds.length + groupIds.length) {
      // A locked group covers its nodes, so only the nodes outside the selected groups get a lock of their own.
      const covered = new Set(groupIds.flatMap((id) => groupMembers(groups, id)));
      lock = { on: allLocked, label: allLocked ? "Unlock all" : "Lock all", disabled: readOnly,
        title: allLocked ? "Unlock everything selected" : "Lock everything selected: it can't be moved or changed",
        run: () => on.onLock({ nodes: allLocked ? nodeIds : nodeIds.filter((id) => !covered.has(id)), groups: groupIds }, !allLocked) };
      if (allLocked) lockedNote = "Everything selected is locked.";
    }
    if (nodeIds.length) remove = { label: `Delete ${deletable.length} node${deletable.length === 1 ? "" : "s"}`, disabled: readOnly || !deletable.length,
      title: deletable.length < nodeIds.length ? `Delete ${deletable.length} (locked nodes are kept)` : "Delete the selected nodes (Delete)", run: () => on.onDeleteNodes(nodeIds) };
  } else if (active?.kind === "node") {
    const node = active.node;
    const group = groups.find((item) => item.node_ids.includes(node.id));
    const by = node.locked ? undefined : lockingGroup(groups, group?.id);
    lockedNote = node.locked ? "This node can't be moved or changed." : by ? `It's in ${by.name}, which is locked. Unlock that group to move or change it.` : "";
    lock = { on: !!node.locked || !!by, label: node.locked || by ? "Unlock node" : "Lock node", disabled: readOnly || !!by,
      title: by ? lockedNote : node.locked ? "Unlock it so it can be moved and changed" : "Lock it so it can't be moved or changed by accident",
      run: () => on.onLock({ nodes: [node.id] }, !node.locked) };
    remove = { label: "Delete node", disabled: readOnly || !!node.locked || !!by, title: "Delete this node (Delete)", run: () => on.onDeleteNodes([node.id]) };
  } else if (active?.kind === "group") {
    const group = active.group;
    const by = group.locked ? undefined : lockingGroup(groups, group.parent_id);
    lockedNote = group.locked ? "This group and everything in it can't be moved or changed." : by ? `It's in ${by.name}, which is locked. Unlock that group to move or change it.` : "";
    lock = { on: !!group.locked || !!by, label: group.locked || by ? "Unlock group" : "Lock group", disabled: readOnly || !!by,
      title: by ? lockedNote : group.locked ? "Unlock it so it can be moved and changed" : "Lock it and everything in it so nothing can be moved or changed by accident",
      run: () => on.onLock({ groups: [group.id] }, !group.locked) };
    const members = groupMembers(groups, group.id);
    remove = { label: "Delete group and its nodes", disabled: readOnly || groupLocked(groups, group.id) || members.some((id) => nodeLocked(graph, id)),
      title: `Delete ${group.name} and the ${members.length} node${members.length === 1 ? "" : "s"} in it`, run: () => on.onDeleteGroup(group.id) };
  } else if (active?.kind === "edge") {
    const lockedEnd = [active.edge.source, active.edge.target].find((id) => nodeLocked(graph, id));
    if (lockedEnd) lockedNote = `${name(lockedEnd)} is locked, so this connection can't change.`;
    remove = { label: "Disconnect", disabled: readOnly || !!lockedEnd, title: "Remove this connection (Delete)", run: () => on.onDisconnect(active.index) };
  }
  return { lock, remove, lockedNote };
}

const SETTINGS_OPEN_KEY = "ducky.workflows.settingsOpen";

/** A node's Settings fold open and shut; folded stays folded for every node on this PC. */
function SettingsFold({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(() => {
    try { return window.localStorage.getItem(SETTINGS_OPEN_KEY) !== "0"; } catch { return true; }
  });
  return <details className="aw-insp-section aw-insp-settings aw-fold" open={open} onToggle={(event) => {
    const next = event.currentTarget.open;
    setOpen(next);
    try { window.localStorage.setItem(SETTINGS_OPEN_KEY, next ? "1" : "0"); } catch { /* private mode */ }
  }}>
    <summary className="aw-pins-title">Settings</summary>
    {children}
  </details>;
}

function NodeDetails({ node, graph, byType, faces, readOnly, workflows, currentId, editing, onEditing, pinsOf, nodeOutputs, liveNodes, lastSteps, team, detailsTab = "settings", codeWide = false, codeFocus, ...on }: Props & EditProps & { node: AutomationGraphNodeDto }) {
  const meta = byType.get(node.type);
  const basedMeta = byType.get(basedOnNode(node)?.type || "");
  const label = nodeLabel(node, meta);
  const group = graph.groups?.find((item) => item.node_ids.includes(node.id));
  const lockedBy = node.locked ? undefined : lockingGroup(graph.groups || [], group?.id);
  const locked = !!node.locked || !!lockedBy;
  const frozen = readOnly || locked;
  const tabsId = useId();
  // A failed code step clicked in the run log: its Code tab, at the error line.
  const reveal = codeFocus && codeFocus.nodeId === node.id ? { line: codeFocus.line, nonce: codeFocus.nonce } : null;
  const tab: DetailsTab = detailsTab;
  const pick = (next: DetailsTab) => on.onDetailsTab?.(next);
  const onTabKey = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    event.stopPropagation();
    const next = tab === "code" ? "settings" : "code";
    pick(next);
    event.currentTarget.querySelector<HTMLElement>(`[data-details-tab="${next}"]`)?.focus({ preventScroll: true });
  };
  return <>
    <DetailsHead icon={<IconPicker icon={node.icon} shown={<NodeIcon meta={meta} node={node} faces={faces} basedMeta={basedMeta} />} label="Node icon" disabled={frozen || !editing} onChange={(icon) => on.onNodeIcon(node.id, icon)} />} kind={[meta?.label || node.type, meta?.group].filter(Boolean).join(" · ")}
      name={label} nameLabel="Node name" description={node.description ?? ""} fallback={meta?.description} editable={!frozen} editing={editing} onEditing={onEditing}
      onSave={(patch) => on.onNodeText(node.id, { ...(patch.name !== undefined ? { label: patch.name } : {}), ...(patch.description !== undefined ? { description: patch.description } : {}) })} />
    {editing && !frozen ? <fieldset className="aw-insp-section aw-insp-look">
      <ColorField label="Color" value={node.color || ""} plainLabel="By kind" className={`aw-swatches--${nodeRole(node, meta)}`} onChange={(color) => on.onNodeColor(node.id, color)} />
    </fieldset> : null}
    {liveNodes?.[node.id] ? <LiveNodeStatus run={liveNodes[node.id]!} /> : null}
    <div className="aw-insp-subtabs" role="tablist" aria-label="Node details" onKeyDown={onTabKey}>
      {(["settings", "code"] as const).map((key) => <button key={key} type="button" role="tab" data-details-tab={key} id={`${tabsId}-${key}`} aria-controls={`${tabsId}-panel`}
        aria-selected={tab === key} tabIndex={tab === key ? 0 : -1} className="aw-insp-subtab" onClick={() => pick(key)}>
        {key === "settings" ? "Settings" : <><span className="aw-code-badge" aria-hidden="true">&lt;/&gt;</span> Code</>}
      </button>)}
    </div>
    <div className="aw-insp-subpanel" role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`}>
      {tab === "code" ? <CodeTab workflowId={currentId || ""} node={node} graph={graph} byType={byType} workflows={workflows}
        pins={pinsOf ? pinsOf(node) : { exec: true, inputs: [], outputs: [] }} readOnly={readOnly} locked={locked} team={!!team}
        lastStep={lastSteps?.[node.id]} reveal={reveal} wide={codeWide} onWide={(wide) => on.onCodeWide?.(wide)}
        onNodeChange={on.onNodeChange} onNodeReplace={(next, label) => on.onNodeReplace?.(next, label)}
        onCodeSession={(phase, id) => on.onCodeSession?.(phase, id)} onRunNode={on.onRunNode} runningNode={on.runningNode} /> : <>
        {pinsOf ? <PinsSection node={node} pins={pinsOf(node)} graph={graph} outputs={nodeOutputs?.[node.id]} frozen={frozen}
          onNodeChange={on.onNodeChange} /> : null}
        {hasNodeSettings(node, meta) ? <SettingsFold>
          <fieldset className="aw-insp-fold" disabled={frozen}>
            <NodeSettings node={node} meta={meta} workflows={workflows} currentId={currentId} onOpen={on.onOpenWorkflow} onChange={on.onNodeChange} />
          </fieldset>
        </SettingsFold> : null}
        {frozen || !group ? null : <div className="aw-insp-actions">
          <button type="button" title="Move it out of its group (Ctrl+Shift+G)" onClick={() => on.onTakeOut(node.id)}>Take out of {group.name}</button>
        </div>}
      </>}
    </div>
  </>;
}

function GroupDetails({ group, graph, byType, faces, readOnly, editing, onEditing, ...on }: Props & EditProps & { group: AutomationGraphGroupDto }) {
  const groups = graph.groups || [];
  const members = groupMembers(groups, group.id);
  const nodes = graph.nodes.filter((node) => members.includes(node.id));
  const parent = groups.find((item) => item.id === group.parent_id);
  const nested = groups.filter((item) => item.parent_id === group.id);
  const lockedBy = group.locked ? undefined : lockingGroup(groups, group.parent_id);
  const frozen = readOnly || !!group.locked || !!lockedBy;
  return <>
    <DetailsHead icon={<IconPicker icon={group.icon} shown={<GroupIcon icon={group.icon} />} label="Group icon" disabled={frozen || !editing} onChange={(icon) => on.onGroupChange(group.id, { icon })} />} kind={`Group · ${nodes.length} node${nodes.length === 1 ? "" : "s"}${parent ? ` · inside ${parent.name}` : ""}`}
      name={group.name} nameLabel="Group name" editable={!frozen} editing={editing} onEditing={onEditing} onSave={(patch) => patch.name && on.onGroupChange(group.id, { name: patch.name })} />
    {editing && !frozen ? <fieldset className="aw-insp-section aw-insp-look">
      <ColorField label="Color" value={group.color || ""} plainLabel="Plain" className="aw-group-color--plain" onChange={(color) => on.onGroupChange(group.id, { color })} />
    </fieldset> : null}
    <div className="aw-insp-section">
      <div className="aw-field">
        <span className="aw-field-label">In this group</span>
        <ul className="aw-insp-members">
          {nested.map((child) => <li key={child.id}><button type="button" onClick={() => on.onSelectNodes(groupMembers(groups, child.id))}><GroupIcon icon={child.icon} /><span>{child.name}</span></button></li>)}
          {nodes.filter((node) => group.node_ids.includes(node.id)).map((node) => <li key={node.id}><button type="button" onClick={() => on.onSelectNodes([node.id])}>
            <NodeIcon meta={byType.get(node.type)} node={node} faces={faces} /><span>{nodeLabel(node, byType.get(node.type))}</span>
          </button></li>)}
        </ul>
      </div>
    </div>
    {frozen ? null : <div className="aw-insp-actions">
      <button type="button" title="Remove the box and keep its nodes (Ctrl+Shift+G)" onClick={() => on.onUngroup(group.id)}>Ungroup</button>
      <button type="button" title={`Move ${group.name} into its own workflow and run it from here. Other workflows can run it too.`} onClick={() => on.onMakeReusable(group.id)}>Make reusable</button>
    </div>}
  </>;
}

function EdgeDetails({ index, edge, graph, byType, readOnly, ...on }: Props & { index: number; edge: AutomationGraphEdgeDto }) {
  const name = (id: string) => { const node = graph.nodes.find((item) => item.id === id); return node ? nodeLabel(node, byType.get(node.type)) : id; };
  const lockedEnd = [edge.source, edge.target].find((id) => nodeLocked(graph, id));
  const frozen = readOnly || !!lockedEnd;
  if (edge.kind === "data") {
    return <>
      <DetailsHead icon={<span className="aw-insp-top-icon" aria-hidden="true"><span className="aw-node-icon"><Icons.GitBranch /></span></span>} kind="Data wire"
        name={`${name(edge.source)} → ${name(edge.target)}`} nameLabel="Data wire" editable={false} />
      <div className="aw-insp-section">
        <div className="aw-field">
          <span className="aw-field-label">Carries</span>
          <span className="aw-pin-from"><strong>{name(edge.source)}</strong> · {edge.source_pin} → <strong>{name(edge.target)}</strong> · {edge.target_pin}</span>
          <span className="aw-field-hint">The value made at the start goes into the input at the end each run.</span>
        </div>
      </div>
    </>;
  }
  return <>
    <DetailsHead icon={<span className="aw-insp-top-icon" aria-hidden="true"><span className="aw-node-icon"><Icons.GitBranch /></span></span>} kind="Connection" name={`${name(edge.source)} → ${name(edge.target)}`} nameLabel="Connection" editable={false} />
    <fieldset className="aw-insp-section" disabled={frozen}>
      <div className="aw-field">
        <span className="aw-field-label">Route</span>
        <ChoiceDropdown aria-label="Connection route" value={edge.kind} options={graph.nodes.find((node) => node.id === edge.source)?.type === "flow.repeat" ? REPEAT_ROUTES : EDGE_ROUTES} onChange={(value) => on.onEdgeRoute(index, value)} size="compact" />
        <span className="aw-field-hint">True and False follow a Branch or an If. Each item and Done follow a For each; Each try and Done follow a Repeat until.</span>
      </div>
    </fieldset>
  </>;
}
