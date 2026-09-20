import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AutomationTemplatePicker } from "./AutomationTemplatePicker";
import type {
  AutomationDto,
  AutomationFieldDto,
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphNodeDto,
  AutomationNodeDto,
  AutomationRunDto,
  AutomationSummaryDto,
  AutomationTemplateDto,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { takePendingGraphFocus } from "../hooks/graphActivity";
import { Icons } from "../icons/Icons";
import { copyText } from "../utils/copyText";
import { formatRunLog, runLogHasContent } from "./runLog";

const NODE_W = 200;
const NODE_H = 76;
const LOG_H_MIN = 140;
const LOG_H_MAX = 560;
const LOG_H_DEFAULT = 220;
const GROUP_ORDER = ["Starting", "Triggers", "Agents", "Duckies", "Tools", "Logic", "Finish"];

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

function groupCatalog(catalog: AutomationNodeDto[], query: string) {
  const q = query.trim().toLowerCase();
  const map = new Map<string, AutomationNodeDto[]>();
  for (const n of catalog) {
    if (q && !`${n.label} ${n.type} ${n.group} ${n.description || ""}`.toLowerCase().includes(q)) continue;
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

export function AutomationsView({ kind = "automation" }: { kind?: "automation" | "pipeline" }) {
  const [rows, setRows] = useState<AutomationSummaryDto[]>([]);
  const [catalog, setCatalog] = useState<AutomationNodeDto[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<AutomationDto | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [expandedId, setExpandedId] = useState("");
  const [pan, setPan] = useState({ x: 40, y: 40 });
  const [zoom, setZoom] = useState(1);
  const [wireFrom, setWireFrom] = useState<string | null>(null);
  const [draftWire, setDraftWire] = useState<{
    sourceId: string;
    fromX: number;
    fromY: number;
    toX: number;
    toY: number;
  } | null>(null);
  const [log, setLog] = useState<AutomationRunDto | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [logCopied, setLogCopied] = useState(false);
  const [logHeight, setLogHeight] = useState(LOG_H_DEFAULT);
  const logResizeRef = useRef<{ startY: number; startH: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [spawn, setSpawn] = useState<{ x: number; y: number; worldX: number; worldY: number } | null>(null);
  const [spawnFilter, setSpawnFilter] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const panRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const wireRef = useRef<{
    sourceId: string;
    dir: "in" | "out";
    fromX: number;
    fromY: number;
  } | null>(null);

  const byType = useMemo(() => {
    const m = new Map<string, AutomationNodeDto>();
    for (const n of catalog) m.set(n.type, n);
    return m;
  }, [catalog]);

  const isPipeline = kind === "pipeline";

  const refreshList = useCallback(async () => {
    const api = getApi();
    const [list, nodes] = await Promise.all(
      isPipeline
        ? [api?.list_pipelines?.(), api?.list_pipeline_nodes?.()]
        : [api?.list_automations?.(), api?.list_automation_nodes?.()],
    );
    setRows((isPipeline ? list?.pipelines : list?.automations) || []);
    setCatalog(nodes?.nodes || []);
  }, [isPipeline]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (!spawn) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSpawn(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [spawn]);

  const loadOne = useCallback(async (id: string) => {
    const api = getApi();
    const res = isPipeline ? await api?.get_pipeline?.(id) : await api?.get_automation?.(id);
    const row = res?.automation || res?.pipeline;
    if (row) {
      setDraft(row);
      setSelectedId(id);
      setSelectedNodeId("");
      setExpandedId("");
      setLog((row.runs || []).slice(-1)[0] || null);
    }
  }, [isPipeline]);

  useEffect(() => {
    const open = (id: string) => {
      if (!id) return;
      void loadOne(id);
      setLogOpen(true);
    };
    const queued = takePendingGraphFocus(kind);
    if (queued) open(queued);
    const onFocus = (ev: Event) => {
      const detail = (ev as CustomEvent<{ kind?: string; id?: string }>).detail;
      if (detail?.kind !== kind || !detail.id) return;
      takePendingGraphFocus(kind);
      open(detail.id);
    };
    window.addEventListener("ducky:focus-graph", onFocus);
    return () => window.removeEventListener("ducky:focus-graph", onFocus);
  }, [kind, loadOne]);

  const persist = useCallback(async (next: AutomationDto) => {
    const api = getApi();
    const payload = { ...next, kind };
    const res = isPipeline ? await api?.save_pipeline?.(payload) : await api?.save_automation?.(payload);
    const row = res?.automation || res?.pipeline;
    if (row) {
      setDraft(row);
      setSelectedId(row.id);
      await refreshList();
      return row;
    }
    return next;
  }, [isPipeline, kind, refreshList]);

  const createNew = useCallback(async () => {
    setPickerOpen(true);
  }, []);

  const createFromTemplate = useCallback(
    async (template: AutomationTemplateDto | null) => {
      const created = await persist({
        id: "",
        name: template?.name || "Untitled",
        description: template?.description || "",
        enabled: true,
        kind,
        graph: template?.graph || emptyGraph(),
      } as AutomationDto);
      setSelectedNodeId("");
      setExpandedId("");
      setLog(null);
      if (created.id) setSelectedId(created.id);
    },
    [persist],
  );

  const graph = draft?.graph || emptyGraph();

  const patchGraph = (fn: (g: AutomationGraphDto) => AutomationGraphDto) => {
    if (!draft) return;
    const next = { ...draft, graph: fn(graph) };
    setDraft(next);
  };

  const addNodeAt = (entry: AutomationNodeDto, worldX: number, worldY: number) => {
    const node: AutomationGraphNodeDto = {
      id: nid(),
      type: entry.type,
      x: worldX,
      y: worldY,
      config: {},
      label: entry.label,
      description: entry.description || "",
    };
    patchGraph((g) => ({ ...g, nodes: [...g.nodes, node] }));
    setSelectedNodeId(node.id);
    setExpandedId("");
    setSpawn(null);
    setSpawnFilter("");
  };

  const saveDraft = () => {
    if (draft) void persist(draft);
  };

  const runTest = async () => {
    if (!draft?.id) return;
    await persist(draft);
    setBusy(true);
    try {
      const res = isPipeline
        ? await getApi()?.run_pipeline?.(draft.id)
        : await getApi()?.run_automation?.(draft.id);
      if (res) {
        setLog(res);
        setLogOpen(true);
      }
    } finally {
      setBusy(false);
    }
  };

  const spawnGroups = useMemo(() => groupCatalog(catalog, spawnFilter), [catalog, spawnFilter]);

  const worldFromClient = (clientX: number, clientY: number) => {
    const board = boardRef.current?.getBoundingClientRect();
    if (!board) return { x: 0, y: 0 };
    return {
      x: (clientX - board.left - pan.x) / zoom,
      y: (clientY - board.top - pan.y) / zoom,
    };
  };

  const onBoardWheel = (e: React.WheelEvent) => {
    if (e.target instanceof Element && e.target.closest(".aw-log-dock, .aw-log-fab")) return;
    e.preventDefault();
    const next = Math.min(4, Math.max(0.2, zoom * (e.deltaY < 0 ? 1.08 : 0.92)));
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

  const onBoardPointerDown = (e: React.PointerEvent) => {
    if (e.target instanceof Element && e.target.closest(".aw-log-dock, .aw-log-fab")) return;
    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      panRef.current = { x: pan.x, y: pan.y, px: e.clientX, py: e.clientY };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } else if (e.button === 0 && e.target === e.currentTarget) {
      setSelectedNodeId("");
      setWireFrom(null);
      setSpawn(null);
    }
  };

  const onBoardPointerMove = (e: React.PointerEvent) => {
    if (wireRef.current) {
      const w = worldFromClient(e.clientX, e.clientY);
      setDraftWire((d) => (d ? { ...d, toX: w.x, toY: w.y } : d));
      return;
    }
    if (panRef.current) {
      setPan({
        x: panRef.current.x + (e.clientX - panRef.current.px),
        y: panRef.current.y + (e.clientY - panRef.current.py),
      });
      return;
    }
    const drag = dragRef.current;
    if (!drag || !draft) return;
    const board = boardRef.current?.getBoundingClientRect();
    if (!board) return;
    const x = (e.clientX - board.left - pan.x) / zoom - drag.dx;
    const y = (e.clientY - board.top - pan.y) / zoom - drag.dy;
    patchGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) => (n.id === drag.id ? { ...n, x, y } : n)),
    }));
  };

  const connectNodes = (sourceId: string, targetId: string) => {
    if (!sourceId || !targetId || sourceId === targetId) return;
    patchGraph((g) => {
      const exists = g.edges.some((x) => x.source === sourceId && x.target === targetId);
      return exists ? g : { ...g, edges: [...g.edges, { source: sourceId, target: targetId, kind: "main" }] };
    });
  };

  const finishWire = (clientX: number, clientY: number) => {
    const w = wireRef.current;
    wireRef.current = null;
    setWireFrom(null);
    setDraftWire(null);
    if (!w) return;
    const stack = document.elementsFromPoint(clientX, clientY);
    let tid = "";
    for (const el of stack) {
      if (!(el instanceof Element)) continue;
      const nodeEl = el.closest("[data-aw-node]");
      const id = nodeEl?.getAttribute("data-aw-node") || "";
      if (id && id !== w.sourceId) {
        tid = id;
        break;
      }
    }
    if (!tid) return;
    if (w.dir === "out") connectNodes(w.sourceId, tid);
    else connectNodes(tid, w.sourceId);
  };

  const endPointer = (e: React.PointerEvent) => {
    if (wireRef.current) finishWire(e.clientX, e.clientY);
    dragRef.current = null;
    panRef.current = null;
  };

  const startWire = (e: React.PointerEvent, node: AutomationGraphNodeDto, dir: "in" | "out") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const fromX = dir === "out" ? node.x + NODE_W : node.x;
    const fromY = node.y + NODE_H / 2;
    wireRef.current = { sourceId: node.id, dir, fromX, fromY };
    setWireFrom(node.id);
    setDraftWire({ sourceId: node.id, fromX, fromY, toX: fromX, toY: fromY });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onBoardContextMenu = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    if (t.closest(".aw-log-dock") || t.closest(".aw-log-fab")) return;
    if (t.closest(".aw-node") || t.closest(".aw-port") || t.closest(".aw-wire")) {
      e.preventDefault();
      return;
    }
    e.preventDefault();
    if (!draft) return;
    const world = worldFromClient(e.clientX, e.clientY);
    setSpawn({ x: e.clientX, y: e.clientY, worldX: world.x, worldY: world.y });
    setSpawnFilter("");
  };

  const startNodeDrag = (e: React.PointerEvent, node: AutomationGraphNodeDto) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const board = boardRef.current?.getBoundingClientRect();
    if (!board) return;
    dragRef.current = {
      id: node.id,
      dx: (e.clientX - board.left - pan.x) / zoom - node.x,
      dy: (e.clientY - board.top - pan.y) / zoom - node.y,
    };
    setSelectedNodeId(node.id);
  };

  const logCount = log?.steps?.length || (runLogHasContent(log) ? 1 : 0);

  return (
    <div className="aw-root">
      <aside className="aw-list">
        <div className="aw-list-head">
          <strong>{isPipeline ? "Pipelines" : "Automations"}</strong>
          <button type="button" className="icon-btn" title="New workflow" onClick={() => void createNew()}>
            +
          </button>
        </div>
        <ul className="aw-list-ul">
          {rows.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                className={`aw-list-row${row.id === selectedId ? " is-active" : ""}`}
                onClick={() => void loadOne(row.id)}
              >
                <span>{row.name || "Untitled"}</span>
                <span className="aw-list-meta">{row.enabled ? "on" : "off"}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div className="aw-main">
        <div className="aw-toolbar">
          {draft ? (
            <>
              <input
                className="aw-name"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                onBlur={saveDraft}
              />
              {isPipeline ? (
                <input
                  className="aw-name"
                  placeholder="Description (so a ducky can pick this recipe)"
                  value={draft.description || ""}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  onBlur={saveDraft}
                />
              ) : null}
              <label className="aw-enable">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(e) => {
                    const next = { ...draft, enabled: e.target.checked };
                    setDraft(next);
                    void persist(next);
                  }}
                />
                Enabled
              </label>
              <button type="button" onClick={saveDraft}>
                Save
              </button>
              <button type="button" onClick={() => void runTest()} disabled={busy || !draft.id}>
                {busy ? "Running…" : "Test"}
              </button>
              <button
                type="button"
                onClick={async () => {
                  if (!draft.id) return;
                  const copy = {
                    ...draft,
                    id: "",
                    name: `${draft.name} copy`,
                  };
                  await persist(copy);
                }}
              >
                Duplicate
              </button>
              <button
                type="button"
                onClick={async () => {
                  if (!draft.id) return;
                  if (isPipeline) await getApi()?.delete_pipeline?.(draft.id);
                  else await getApi()?.delete_automation?.(draft.id);
                  setDraft(null);
                  setSelectedId("");
                  await refreshList();
                }}
              >
                Delete
              </button>
            </>
          ) : (
            <span className="aw-empty-hint">
              {isPipeline
                ? "Create a pipeline — Chat start, Agent, plugin nodes, Finish. Right-click the canvas to add nodes."
                : "Create a workflow or pick a template — right-click the canvas to add nodes."}
            </span>
          )}
        </div>
        <div
          ref={boardRef}
          className="aw-board"
          onWheel={onBoardWheel}
          onPointerDown={onBoardPointerDown}
          onPointerMove={onBoardPointerMove}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
          onContextMenu={onBoardContextMenu}
        >
          <div className="aw-world" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
            <svg className="aw-wires" width={8000} height={8000}>
              {graph.edges.map((e, i) => {
                const a = graph.nodes.find((n) => n.id === e.source);
                const b = graph.nodes.find((n) => n.id === e.target);
                if (!a || !b) return null;
                const x1 = a.x + NODE_W;
                const y1 = a.y + NODE_H / 2;
                const x2 = b.x;
                const y2 = b.y + NODE_H / 2;
                const c = Math.max(40, (x2 - x1) / 2);
                return (
                  <path
                    key={`${e.source}-${e.target}-${e.kind}-${i}`}
                    d={`M ${x1} ${y1} C ${x1 + c} ${y1}, ${x2 - c} ${y2}, ${x2} ${y2}`}
                    className={`aw-wire aw-wire--${e.kind}${selectedNodeId === e.source || selectedNodeId === e.target ? " is-hot" : ""}`}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      patchGraph((g) => ({
                        ...g,
                        edges: g.edges.filter((x) => !(x.source === e.source && x.target === e.target && x.kind === e.kind)),
                      }));
                    }}
                  />
                );
              })}
              {draftWire ? (
                <path
                  d={`M ${draftWire.fromX} ${draftWire.fromY} C ${draftWire.fromX + 40} ${draftWire.fromY}, ${draftWire.toX - 40} ${draftWire.toY}, ${draftWire.toX} ${draftWire.toY}`}
                  className="aw-wire aw-wire--draft"
                />
              ) : null}
            </svg>
            {graph.nodes.map((node) => {
              const meta = byType.get(node.type);
              const expanded = expandedId === node.id;
              return (
                <div
                  key={node.id}
                  data-aw-node={node.id}
                  className={`aw-node aw-node--${meta?.role || "action"}${selectedNodeId === node.id ? " is-selected" : ""}${wireFrom === node.id ? " is-wiring" : ""}${expanded ? " is-expanded" : ""}`}
                  style={{ left: node.x, top: node.y }}
                >
                  <button
                    type="button"
                    className="aw-port aw-port--in"
                    data-aw-node={node.id}
                    onPointerDown={(e) => startWire(e, node, "in")}
                    onPointerMove={onBoardPointerMove}
                    onPointerUp={endPointer}
                    onPointerCancel={endPointer}
                  />
                  <div className="aw-node-card">
                    <div className="aw-node-body" onPointerDown={(e) => startNodeDrag(e, node)}>
                      <strong>{node.label || meta?.label || node.type}</strong>
                      <small>{node.description || meta?.description || node.type}</small>
                    </div>
                    <button
                      type="button"
                      className="aw-node-expand-toggle"
                      aria-expanded={expanded}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedNodeId(node.id);
                        setExpandedId(expanded ? "" : node.id);
                      }}
                    >
                      {expanded ? "Close" : "Expand"}
                    </button>
                    {expanded ? (
                      <div className="aw-node-props aw-node-props--open" onPointerDown={(e) => e.stopPropagation()}>
                        <NodeInspector
                          node={node}
                          meta={meta}
                          edges={graph.edges.filter((e) => e.source === node.id)}
                          onChange={(next) =>
                            patchGraph((g) => ({
                              ...g,
                              nodes: g.nodes.map((n) => (n.id === next.id ? next : n)),
                            }))
                          }
                          onEdgeKind={(target, kind) =>
                            patchGraph((g) => ({
                              ...g,
                              edges: g.edges.map((e) =>
                                e.source === node.id && e.target === target ? { ...e, kind } : e,
                              ),
                            }))
                          }
                          onDelete={() => {
                            patchGraph((g) => ({
                              nodes: g.nodes.filter((n) => n.id !== node.id),
                              edges: g.edges.filter((e) => e.source !== node.id && e.target !== node.id),
                            }));
                            setSelectedNodeId("");
                            setExpandedId("");
                          }}
                        />
                      </div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="aw-port aw-port--out"
                    data-aw-node={node.id}
                    onPointerDown={(e) => startWire(e, node, "out")}
                    onPointerMove={onBoardPointerMove}
                    onPointerUp={endPointer}
                    onPointerCancel={endPointer}
                  />
                </div>
              );
            })}
          </div>
        </div>
        <button
          type="button"
          className={`aw-log-fab${logOpen ? " is-open" : ""}`}
          title={logOpen ? "Hide run log" : "Run log"}
          aria-expanded={logOpen}
          onClick={() => setLogOpen((v) => !v)}
        >
          <Icons.Sliders />
          {logCount ? <span className="aw-log-fab-badge">{logCount}</span> : null}
        </button>
        {logOpen ? (
          <div
            className="aw-log-dock"
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
              <button type="button" className="icon-btn" title="Hide" onClick={() => setLogOpen(false)}>
                ×
              </button>
            </div>
            <div className="aw-log-dock-body selectable-text">
              {runLogHasContent(log) ? (
                <ol>
                  {log?.ok === false && log.error ? <li className="is-err">{log.error}</li> : null}
                  {(log?.steps || []).map((s, i) => (
                    <li key={i} className={s.ok === false ? "is-err" : ""}>
                      {s.label || s.type} {s.ok === false ? `— ${s.error}` : "ok"}
                    </li>
                  ))}
                </ol>
              ) : (
                <p>
                  {isPipeline
                    ? "Test a pipeline to see steps here. Finish posts back to the calling chat."
                    : "Test a graph to see steps here. Timers only fire while the panel is running."}
                </p>
              )}
            </div>
          </div>
        ) : null}
      </div>
      {spawn ? (
        <div className="aw-spawn-scrim" onMouseDown={() => setSpawn(null)}>
          <div
            className="aw-spawn-menu"
            role="dialog"
            aria-label="Add node"
            style={{ left: spawn.x, top: spawn.y }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <input
              className="aw-spawn-search"
              autoFocus
              placeholder="Filter nodes"
              value={spawnFilter}
              onChange={(e) => setSpawnFilter(e.target.value)}
            />
            <p className="aw-spawn-hint">Right-click the canvas to add a node.</p>
            <div className="aw-spawn-scroll">
              {spawnGroups.length ? (
                spawnGroups.map(([name, tiles]) => (
                  <details key={name} className="aw-acc" open>
                    <summary>{name}</summary>
                    {tiles.map((t) => (
                      <button
                        key={t.type}
                        type="button"
                        className="aw-tile"
                        onClick={() => addNodeAt(t, spawn.worldX, spawn.worldY)}
                      >
                        <span>{t.label}</span>
                        <small>{t.description}</small>
                      </button>
                    ))}
                  </details>
                ))
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
        system={kind}
      />
    </div>
  );
}

function FieldInput({
  field,
  node,
  pluginId,
  onChange,
}: {
  field: AutomationFieldDto;
  node: AutomationGraphNodeDto;
  pluginId?: string;
  onChange: (n: AutomationGraphNodeDto) => void;
}) {
  const value = String(node.config[field.id] ?? "");
  const set = (next: string | number) =>
    onChange({ ...node, config: { ...node.config, [field.id]: next } });
  const [models, setModels] = useState<Array<{ id: string; name?: string }>>([]);
  const provider =
    field.provider || (pluginId === "google" ? "gemini" : pluginId) || "";

  useEffect(() => {
    if (field.type !== "model" || !provider) return;
    let cancelled = false;
    void getApi()
      ?.get_models(provider)
      ?.then((rows) => {
        if (!cancelled && Array.isArray(rows)) setModels(rows);
      })
      ?.catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [field.type, provider]);

  if (field.type === "textarea") {
    return (
      <textarea rows={4} value={value} onChange={(e) => set(e.target.value)} />
    );
  }
  if (field.type === "select" || field.type === "model") {
    const opts =
      field.type === "model"
        ? models.map((m) => ({ id: m.id, label: m.name || m.id }))
        : field.options || [];
    return (
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value="">(default)</option>
        {opts.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label || o.id}
          </option>
        ))}
        {value && !opts.some((o) => o.id === value) ? <option value={value}>{value}</option> : null}
      </select>
    );
  }
  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      value={value}
      onChange={(e) => set(field.type === "number" ? Number(e.target.value) : e.target.value)}
    />
  );
}

function NodeInspector({
  node,
  meta,
  edges,
  onChange,
  onEdgeKind,
  onDelete,
}: {
  node: AutomationGraphNodeDto;
  meta?: AutomationNodeDto;
  edges: AutomationGraphEdgeDto[];
  onChange: (n: AutomationGraphNodeDto) => void;
  onEdgeKind: (target: string, kind: string) => void;
  onDelete: () => void;
}) {
  const fields = meta?.config_fields || [];
  return (
    <div className="aw-insp-form">
      <label>
        Label
        <input value={node.label || ""} onChange={(e) => onChange({ ...node, label: e.target.value })} />
      </label>
      <label>
        Description
        <textarea
          value={node.description || ""}
          onChange={(e) => onChange({ ...node, description: e.target.value })}
          rows={3}
        />
      </label>
      {fields.map((f) => (
        <label key={f.id}>
          {f.label || f.id}
          <FieldInput field={f} node={node} pluginId={meta?.plugin_id} onChange={onChange} />
        </label>
      ))}
      {edges.length > 0 ? (
        <div className="aw-insp-edges">
          <span>Wires</span>
          {edges.map((e) => (
            <label key={`${e.target}-${e.kind}`}>
              → {e.target}
              <select value={e.kind} onChange={(ev) => onEdgeKind(e.target, ev.target.value)}>
                <option value="main">main</option>
                <option value="true">true</option>
                <option value="false">false</option>
                <option value="each">each</option>
                <option value="done">done</option>
              </select>
            </label>
          ))}
        </div>
      ) : null}
      <button type="button" className="aw-danger" onClick={onDelete}>
        Delete node
      </button>
    </div>
  );
}
