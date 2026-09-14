import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AutomationDto,
  AutomationGraphDto,
  AutomationGraphEdgeDto,
  AutomationGraphNodeDto,
  AutomationNodeDto,
  AutomationRunDto,
  AutomationSummaryDto,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";

const NODE_W = 200;
const NODE_H = 72;
const GROUP_ORDER = ["Starting", "Triggers", "Duckies", "Tools", "Logic"];

function emptyGraph(): AutomationGraphDto {
  return { nodes: [], edges: [] };
}

function nid(): string {
  return `n${Math.random().toString(36).slice(2, 10)}`;
}

export function AutomationsView() {
  const [rows, setRows] = useState<AutomationSummaryDto[]>([]);
  const [catalog, setCatalog] = useState<AutomationNodeDto[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<AutomationDto | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [pan, setPan] = useState({ x: 40, y: 40 });
  const [zoom, setZoom] = useState(1);
  const [wireFrom, setWireFrom] = useState<string | null>(null);
  const [log, setLog] = useState<AutomationRunDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const boardRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const panRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  const byType = useMemo(() => {
    const m = new Map<string, AutomationNodeDto>();
    for (const n of catalog) m.set(n.type, n);
    return m;
  }, [catalog]);

  const refreshList = useCallback(async () => {
    const api = getApi();
    const [list, nodes] = await Promise.all([
      api?.list_automations?.(),
      api?.list_automation_nodes?.(),
    ]);
    setRows(list?.automations || []);
    setCatalog(nodes?.nodes || []);
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const loadOne = useCallback(async (id: string) => {
    const api = getApi();
    const res = await api?.get_automation?.(id);
    if (res?.automation) {
      setDraft(res.automation);
      setSelectedId(id);
      setSelectedNodeId("");
      setLog((res.automation.runs || []).slice(-1)[0] || null);
    }
  }, []);

  const persist = useCallback(async (next: AutomationDto) => {
    const api = getApi();
    const res = await api?.save_automation?.(next);
    if (res?.automation) {
      setDraft(res.automation);
      setSelectedId(res.automation.id);
      await refreshList();
      return res.automation;
    }
    return next;
  }, [refreshList]);

  const createNew = useCallback(async () => {
    const created = await persist({
      id: "",
      name: "Untitled",
      enabled: true,
      graph: emptyGraph(),
    } as AutomationDto);
    setSelectedNodeId("");
    setLog(null);
    if (created.id) setSelectedId(created.id);
  }, [persist]);

  const graph = draft?.graph || emptyGraph();
  const selectedNode = graph.nodes.find((n) => n.id === selectedNodeId) || null;

  const patchGraph = (fn: (g: AutomationGraphDto) => AutomationGraphDto) => {
    if (!draft) return;
    const next = { ...draft, graph: fn(graph) };
    setDraft(next);
  };

  const addNode = (entry: AutomationNodeDto) => {
    const board = boardRef.current?.getBoundingClientRect();
    const x = ((board?.width || 400) / 2 - pan.x) / zoom - NODE_W / 2;
    const y = ((board?.height || 300) / 2 - pan.y) / zoom - NODE_H / 2;
    const node: AutomationGraphNodeDto = {
      id: nid(),
      type: entry.type,
      x,
      y,
      config: {},
      label: entry.label,
      description: entry.description || "",
    };
    patchGraph((g) => ({ ...g, nodes: [...g.nodes, node] }));
    setSelectedNodeId(node.id);
  };

  const saveDraft = () => {
    if (draft) void persist(draft);
  };

  const runTest = async () => {
    if (!draft?.id) return;
    await persist(draft);
    setBusy(true);
    try {
      const res = await getApi()?.run_automation?.(draft.id);
      if (res) setLog(res);
    } finally {
      setBusy(false);
    }
  };

  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const map = new Map<string, AutomationNodeDto[]>();
    for (const n of catalog) {
      if (q && !`${n.label} ${n.type} ${n.group}`.toLowerCase().includes(q)) continue;
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
  }, [catalog, filter]);

  const onBoardWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const next = Math.min(4, Math.max(0.2, zoom * (e.deltaY < 0 ? 1.08 : 0.92)));
    setZoom(next);
  };

  const onBoardPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || e.button === 2 || (e.button === 0 && e.altKey)) {
      panRef.current = { x: pan.x, y: pan.y, px: e.clientX, py: e.clientY };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } else if (e.target === e.currentTarget) {
      setSelectedNodeId("");
      setWireFrom(null);
    }
  };

  const onBoardPointerMove = (e: React.PointerEvent) => {
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

  const endPointer = () => {
    dragRef.current = null;
    panRef.current = null;
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

  const clickPort = (e: React.MouseEvent, nodeId: string, dir: "in" | "out") => {
    e.stopPropagation();
    if (dir === "out") {
      setWireFrom(nodeId);
      return;
    }
    if (wireFrom && wireFrom !== nodeId) {
      const edge: AutomationGraphEdgeDto = { source: wireFrom, target: nodeId, kind: "main" };
      patchGraph((g) => {
        const exists = g.edges.some((x) => x.source === edge.source && x.target === edge.target && x.kind === edge.kind);
        return exists ? g : { ...g, edges: [...g.edges, edge] };
      });
      setWireFrom(null);
    }
  };

  return (
    <div className="aw-root">
      <aside className="aw-list">
        <div className="aw-list-head">
          <strong>Automations</strong>
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
      <aside className="aw-palette">
        <input
          className="aw-palette-filter"
          placeholder="Filter nodes"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {groups.map(([name, tiles]) => (
          <details key={name} className="aw-acc" open={name === "Starting" || name === "Duckies"}>
            <summary>{name}</summary>
            {tiles.map((t) => (
              <button key={t.type} type="button" className="aw-tile" onClick={() => addNode(t)} disabled={!draft}>
                <span>{t.label}</span>
                <small>{t.description}</small>
              </button>
            ))}
          </details>
        ))}
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
                  await getApi()?.delete_automation?.(draft.id);
                  setDraft(null);
                  setSelectedId("");
                  await refreshList();
                }}
              >
                Delete
              </button>
            </>
          ) : (
            <span className="aw-empty-hint">Create a workflow or pick one — empty graphs are valid.</span>
          )}
        </div>
        <div
          ref={boardRef}
          className="aw-board"
          onWheel={onBoardWheel}
          onPointerDown={onBoardPointerDown}
          onPointerMove={onBoardPointerMove}
          onPointerUp={endPointer}
          onContextMenu={(e) => e.preventDefault()}
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
            </svg>
            {graph.nodes.map((node) => {
              const meta = byType.get(node.type);
              return (
                <div
                  key={node.id}
                  className={`aw-node aw-node--${meta?.role || "action"}${selectedNodeId === node.id ? " is-selected" : ""}${wireFrom === node.id ? " is-wiring" : ""}`}
                  style={{ left: node.x, top: node.y }}
                  onPointerDown={(e) => startNodeDrag(e, node)}
                >
                  <button type="button" className="aw-port aw-port--in" onClick={(e) => clickPort(e, node.id, "in")} />
                  <div className="aw-node-body">
                    <strong>{node.label || meta?.label || node.type}</strong>
                    <small>{node.description || meta?.description || node.type}</small>
                  </div>
                  <button type="button" className="aw-port aw-port--out" onClick={(e) => clickPort(e, node.id, "out")} />
                </div>
              );
            })}
          </div>
        </div>
        <div className="aw-log">
          <strong>Run log</strong>
          {log?.steps?.length ? (
            <ol>
              {log.steps.map((s, i) => (
                <li key={i} className={s.ok === false ? "is-err" : ""}>
                  {s.label || s.type} {s.ok === false ? `— ${s.error}` : "ok"}
                </li>
              ))}
            </ol>
          ) : (
            <p>Test a graph to see steps here. Timers only fire while the panel is running.</p>
          )}
        </div>
      </div>
      <aside className="aw-inspector">
        <div className="aw-inspector-head">
          <Icons.Sliders />
          <span>Inspector</span>
        </div>
        {selectedNode && draft ? (
          <NodeInspector
            node={selectedNode}
            meta={byType.get(selectedNode.type)}
            edges={graph.edges.filter((e) => e.source === selectedNode.id)}
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
                  e.source === selectedNode.id && e.target === target ? { ...e, kind } : e,
                ),
              }))
            }
            onDelete={() => {
              patchGraph((g) => ({
                nodes: g.nodes.filter((n) => n.id !== selectedNode.id),
                edges: g.edges.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id),
              }));
              setSelectedNodeId("");
            }}
          />
        ) : (
          <p className="aw-empty-hint">Select a node. Click an output port, then an input port, to wire.</p>
        )}
      </aside>
    </div>
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
          {f.type === "textarea" ? (
            <textarea
              rows={4}
              value={String(node.config[f.id] ?? "")}
              onChange={(e) => onChange({ ...node, config: { ...node.config, [f.id]: e.target.value } })}
            />
          ) : (
            <input
              type={f.type === "number" ? "number" : "text"}
              value={String(node.config[f.id] ?? "")}
              onChange={(e) =>
                onChange({
                  ...node,
                  config: {
                    ...node.config,
                    [f.id]: f.type === "number" ? Number(e.target.value) : e.target.value,
                  },
                })
              }
            />
          )}
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
