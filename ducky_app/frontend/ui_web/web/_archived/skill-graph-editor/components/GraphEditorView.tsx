import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphNode, StudioView } from "../model/graphTypes";
import type { HitRegion } from "./graphCanvasDraw";
import { drawSkillGraph } from "./graphCanvasDraw";
import { NodeOverlay } from "./NodeOverlay";
import { SchemaPreviewView } from "./SchemaPreviewView";
import { Icons } from "./icons";
import { useGraphViewport } from "../hooks/useGraphViewport";
import { ZOOM_STEP } from "../constants/layout";
import { readSpsCanvasTheme } from "../utils/canvasTheme";
import { useAppearance } from "../../theme/AppearanceContext";
import { getApi } from "../../hooks/usePanelApi";
import * as api from "../api/skillPackStudioApi";

interface GraphEditorViewProps {
  packId: string;
  packLabel: string;
  packKind: string;
  nodes: Record<string, GraphNode>;
  viewMode: StudioView;
  busy: boolean;
  statusMsg: string;
  onBack: () => void;
  onViewModeChange: (mode: StudioView) => void;
  onNodesChange: (nodes: Record<string, GraphNode>) => void;
  onReload: () => void;
  onStatus: (msg: string) => void;
}

type DragInfo =
  | { type: "pan"; startX: number; startY: number; initPanX: number; initPanY: number }
  | {
      type: "node";
      id: string;
      startX: number;
      startY: number;
      initX: number;
      initY: number;
    };

export function GraphEditorView({
  packId,
  packLabel,
  packKind,
  nodes,
  viewMode,
  busy,
  statusMsg,
  onBack,
  onViewModeChange,
  onNodesChange,
  onReload,
  onStatus,
}: GraphEditorViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hitRegionsRef = useRef<HitRegion[]>([]);
  const layoutTimerRef = useRef<number | null>(null);

  const { viewport, setPan, resetViewport, zoomBy, zoomWheel, screenToWorld } = useGraphViewport();
  const { cssVars } = useAppearance();
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});
  const [dragInfo, setDragInfo] = useState<DragInfo | null>(null);
  const [cursor, setCursor] = useState("grab");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [exportPath, setExportPath] = useState<string | null>(null);

  const treeNodes = useMemo(() => Object.values(nodes), [nodes]);

  const scheduleLayoutSave = useCallback(
    (nextNodes: Record<string, GraphNode>) => {
      if (layoutTimerRef.current) window.clearTimeout(layoutTimerRef.current);
      layoutTimerRef.current = window.setTimeout(() => {
        void api.saveLayout(packId, api.buildLayoutFromNodes(nextNodes)).catch((e: Error) => {
          onStatus(e.message);
        });
      }, 400);
    },
    [packId, onStatus],
  );

  const patchNodeLocal = useCallback(
    (nodeId: string, patch: Partial<GraphNode>) => {
      onNodesChange({
        ...nodes,
        [nodeId]: { ...nodes[nodeId], ...patch },
      });
    },
    [nodes, onNodesChange],
  );

  const saveNodeMeta = useCallback(
    async (nodeId: string) => {
      const node = nodes[nodeId];
      if (!node) return;
      try {
        await api.saveNodeMeta(packId, nodeId, {
          label: node.title,
          description: node.description,
          default_enabled: node.defaultEnabled,
          load_condition: node.loadCondition || null,
          parent_id: node.parentId,
        });
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [nodes, packId, onStatus],
  );

  const handlePatch = useCallback(
    (nodeId: string, patch: Partial<GraphNode>) => {
      patchNodeLocal(nodeId, patch);
    },
    [patchNodeLocal],
  );

  const handleMetaBlur = useCallback(
    (nodeId: string) => {
      void saveNodeMeta(nodeId);
    },
    [saveNodeMeta],
  );

  const handleContentSave = useCallback(
    async (nodeId: string, text: string) => {
      try {
        await api.saveNodeContent(packId, nodeId, text);
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [packId, onStatus],
  );

  const handleAddChild = useCallback(
    async (parentId: string) => {
      const parent = nodes[parentId];
      if (!parent) return;
      const siblings = Object.values(nodes).filter((n) => n.parentId === parentId);
      const offsetY = siblings.length * 160 - 80;
      try {
        const newId = await api.createChildNode(packId, parentId, {
          label: "New sub-skill",
          description: "",
          loadCondition: "",
        });
        const newNode: GraphNode = {
          id: newId,
          parentId,
          type: "skill",
          title: "New sub-skill",
          description: "",
          loadCondition: "",
          content: "",
          defaultEnabled: false,
          alwaysOn: false,
          x: parent.x + 400,
          y: parent.y + offsetY + 50,
        };
        const next = { ...nodes, [newId]: newNode };
        onNodesChange(next);
        scheduleLayoutSave(next);
        setExpandedNodes((prev) => ({ ...prev, [newId]: true }));
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [nodes, packId, onNodesChange, scheduleLayoutSave, onStatus],
  );

  const handleDelete = useCallback(
    async (nodeId: string) => {
      if (confirmDeleteId !== nodeId) {
        setConfirmDeleteId(nodeId);
        return;
      }
      try {
        await api.deleteNode(packId, nodeId);
        setConfirmDeleteId(null);
        onReload();
      } catch (e) {
        onStatus(e instanceof Error ? e.message : String(e));
      }
    },
    [confirmDeleteId, packId, onReload, onStatus],
  );

  const handleExport = useCallback(async () => {
    try {
      const res = await api.exportPack(packId);
      if (res.cancelled) return;
      if (!res.ok) throw new Error(res.error ?? "Export failed");
      const name = res.filename ?? `${packId}.ducky-skill-pack`;
      if (res.path) {
        setExportPath(res.path);
        onStatus(`Saved ${name}`);
      } else {
        setExportPath(null);
        onStatus(`Downloaded ${name}`);
      }
    } catch (e) {
      setExportPath(null);
      onStatus(e instanceof Error ? e.message : String(e));
    }
  }, [packId, onStatus]);

  const handleOpenExportFolder = useCallback(() => {
    if (!exportPath) return;
    void getApi()?.open_path_in_explorer?.(exportPath);
  }, [exportPath]);

  useEffect(() => {
    if (viewMode !== "graph") return;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const theme = readSpsCanvasTheme(container);

    drawSkillGraph({
      ctx,
      width: rect.width,
      height: rect.height,
      dpr,
      nodes: treeNodes,
      expandedNodes,
      dragNodeId: dragInfo?.type === "node" ? dragInfo.id : null,
      pan: viewport.pan,
      zoom: viewport.zoom,
      hitRegions: hitRegionsRef.current,
      theme,
    });
  }, [treeNodes, expandedNodes, viewport, viewMode, dragInfo, cssVars]);

  useEffect(() => {
    if (viewMode !== "graph") return;
    const container = containerRef.current;
    if (!container) return;

    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const rect = container.getBoundingClientRect();
      zoomWheel(e.deltaY, e.clientX - rect.left, e.clientY - rect.top);
    };

    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, [viewMode, zoomWheel]);

  const zoomAtCenter = useCallback(
    (delta: number) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      zoomBy(delta, rect.width / 2, rect.height / 2);
    },
    [zoomBy],
  );

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (viewMode !== "graph") return;
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, button, label")) return;

      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      const world = screenToWorld(clickX, clickY);

      for (let i = hitRegionsRef.current.length - 1; i >= 0; i--) {
        const r = hitRegionsRef.current[i];
        if (
          world.x >= r.x &&
          world.x <= r.x + r.w &&
          world.y >= r.y &&
          world.y <= r.y + r.h
        ) {
          if (r.type === "header") {
            const node = nodes[r.id];
            if (!node) return;
            setDragInfo({
              type: "node",
              id: r.id,
              startX: clickX,
              startY: clickY,
              initX: node.x,
              initY: node.y,
            });
            e.currentTarget.setPointerCapture(e.pointerId);
            return;
          }
          if (r.type === "toggle") {
            setExpandedNodes((prev) => ({ ...prev, [r.id]: !prev[r.id] }));
            setConfirmDeleteId(null);
            return;
          }
          if (r.type === "add") {
            void handleAddChild(r.id);
            return;
          }
        }
      }

      setDragInfo({
        type: "pan",
        startX: clickX,
        startY: clickY,
        initPanX: viewport.pan.x,
        initPanY: viewport.pan.y,
      });
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [nodes, screenToWorld, viewport.pan, handleAddChild, viewMode],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (viewMode !== "graph") return;
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      if (dragInfo) {
        const deltaX = x - dragInfo.startX;
        const deltaY = y - dragInfo.startY;
        if (dragInfo.type === "pan") {
          setPan({ x: dragInfo.initPanX + deltaX, y: dragInfo.initPanY + deltaY });
        } else {
          const next = {
            ...nodes,
            [dragInfo.id]: {
              ...nodes[dragInfo.id],
              x: dragInfo.initX + deltaX / viewport.zoom,
              y: dragInfo.initY + deltaY / viewport.zoom,
            },
          };
          onNodesChange(next);
        }
        return;
      }

      const world = screenToWorld(x, y);
      let newCursor = "grab";
      for (let i = hitRegionsRef.current.length - 1; i >= 0; i--) {
        const r = hitRegionsRef.current[i];
        if (world.x >= r.x && world.x <= r.x + r.w && world.y >= r.y && world.y <= r.y + r.h) {
          if (r.type === "toggle" || r.type === "add") newCursor = "pointer";
          break;
        }
      }
      setCursor(newCursor);
    },
    [dragInfo, nodes, onNodesChange, screenToWorld, setPan, viewport.zoom, viewMode],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (dragInfo?.type === "node") {
        scheduleLayoutSave(nodes);
        void saveNodeMeta(dragInfo.id);
      }
      setDragInfo(null);
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    },
    [dragInfo, nodes, scheduleLayoutSave, saveNodeMeta],
  );

  const zoomPercent = Math.round(viewport.zoom * 100);
  const graphBodyCursorClass =
    dragInfo?.type === "pan"
      ? "sps-graph-body is-grabbing"
      : cursor === "pointer"
        ? "sps-graph-body is-pointer"
        : "sps-graph-body is-grab";
  const canvasCursorClass =
    dragInfo?.type === "pan" ? "sps-canvas is-grabbing" : cursor === "pointer" ? "sps-canvas is-pointer" : "sps-canvas is-grab";

  return (
    <div className="sps-graph-editor">
      <div className="sps-graph-toolbar">
        <div className="sps-graph-toolbar-left">
          <button type="button" className="sps-btn sps-btn--ghost" onClick={onBack}>
            <Icons.ArrowLeft className="sps-icon-sm" /> Packs
          </button>
          <span className="sps-toolbar-divider" />
          <span className="sps-graph-pack-name">
            <Icons.Folder className="sps-icon-sm sps-icon-indigo" />
            {packLabel}
          </span>
          <span className="sps-pack-kind-badge">{packKind}</span>
        </div>
        <div className="sps-graph-toolbar-right">
          <div className="sps-zoom-controls">
            <button
              type="button"
              className="sps-btn sps-btn--ghost sps-btn--compact"
              onClick={() => zoomAtCenter(-ZOOM_STEP)}
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              className="sps-btn sps-btn--ghost sps-btn--compact"
              onClick={() => resetViewport()}
              title="Reset zoom"
            >
              {zoomPercent}%
            </button>
            <button
              type="button"
              className="sps-btn sps-btn--ghost sps-btn--compact"
              onClick={() => zoomAtCenter(ZOOM_STEP)}
              title="Zoom in"
            >
              +
            </button>
          </div>
          <button type="button" className="sps-btn sps-btn--ghost" disabled={busy} onClick={() => void handleExport()}>
            <Icons.Download className="sps-icon-sm" /> Export
          </button>
          <button
            type="button"
            className="sps-btn sps-btn--ghost"
            onClick={() => onViewModeChange(viewMode === "schema" ? "graph" : "schema")}
          >
            <Icons.Code className="sps-icon-sm" />
            {viewMode === "schema" ? "Graph" : "Schema"}
          </button>
        </div>
      </div>

      {statusMsg ? (
        <div className="sps-status-row">
          <div className="sps-status-msg">{statusMsg}</div>
          {exportPath ? (
            <button type="button" className="sps-btn sps-btn--ghost sps-btn--compact" onClick={handleOpenExportFolder}>
              Open folder
            </button>
          ) : null}
        </div>
      ) : null}

      <div
        className={viewMode === "graph" ? graphBodyCursorClass : "sps-graph-body"}
        ref={containerRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
      >
        {viewMode === "graph" ? (
          <>
            <canvas ref={canvasRef} className={canvasCursorClass} />
            <NodeOverlay
              nodes={treeNodes}
              expandedNodes={expandedNodes}
              pan={viewport.pan}
              zoom={viewport.zoom}
              confirmDeleteId={confirmDeleteId}
              onPatch={(id, patch) => {
                handlePatch(id, patch);
                if (patch.defaultEnabled !== undefined) void saveNodeMeta(id);
              }}
              onMetaBlur={handleMetaBlur}
              onDelete={(id) => void handleDelete(id)}
              onContentSave={(id, text) => void handleContentSave(id, text)}
            />
          </>
        ) : (
          <SchemaPreviewView packLabel={packLabel} nodes={treeNodes} />
        )}
      </div>
    </div>
  );
}
