import { useEffect, useRef } from "react";
import type { GraphNode } from "../model/graphTypes";
import { HEADER_HEIGHT, NODE_WIDTH } from "../constants/layout";
import { DefaultEnabledToggle } from "./DefaultEnabledToggle";
import { ChildConditionField } from "./ChildConditionField";
import { Icons } from "./icons";

interface NodeOverlayProps {
  nodes: GraphNode[];
  expandedNodes: Record<string, boolean>;
  pan: { x: number; y: number };
  zoom: number;
  confirmDeleteId: string | null;
  onPatch: (nodeId: string, patch: Partial<GraphNode>) => void;
  onMetaBlur: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
  onContentSave: (nodeId: string, text: string) => void;
}

export function NodeOverlay({
  nodes,
  expandedNodes,
  pan,
  zoom,
  confirmDeleteId,
  onPatch,
  onMetaBlur,
  onDelete,
  onContentSave,
}: NodeOverlayProps) {
  const layerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.style.setProperty("--sps-pan-x", `${pan.x}px`);
    layer.style.setProperty("--sps-pan-y", `${pan.y}px`);
    layer.style.setProperty("--sps-zoom", String(zoom));
  }, [pan, zoom]);

  return (
    <div ref={layerRef} className="sps-overlay-layer">
      {nodes.map((node) => {
        if (!expandedNodes[node.id]) return null;
        const isRoot = !node.parentId;
        return (
          <NodeOverlayCard
            key={`overlay-${node.id}`}
            node={node}
            isRoot={isRoot}
            confirmDelete={confirmDeleteId === node.id}
            onPatch={onPatch}
            onMetaBlur={onMetaBlur}
            onDelete={onDelete}
            onContentSave={onContentSave}
          />
        );
      })}
    </div>
  );
}

function NodeOverlayCard({
  node,
  isRoot,
  confirmDelete,
  onPatch,
  onMetaBlur,
  onDelete,
  onContentSave,
}: {
  node: GraphNode;
  isRoot: boolean;
  confirmDelete: boolean;
  onPatch: (nodeId: string, patch: Partial<GraphNode>) => void;
  onMetaBlur: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
  onContentSave: (nodeId: string, text: string) => void;
}) {
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    el.style.setProperty("--sps-node-x", `${node.x}px`);
    el.style.setProperty("--sps-node-y", `${node.y + HEADER_HEIGHT}px`);
    el.style.setProperty("--sps-node-w", `${NODE_WIDTH}px`);
  }, [node.x, node.y]);

  return (
    <div ref={cardRef} className="sps-node-overlay">
      <div className="sps-field">
        <label className="sps-label">Name</label>
        <input
          className="sps-input"
          value={node.title}
          onChange={(e) => onPatch(node.id, { title: e.target.value })}
          onBlur={() => onMetaBlur(node.id)}
          placeholder="Node name"
        />
      </div>
      <div className="sps-field">
        <label className="sps-label">Summary</label>
        <textarea
          className="sps-textarea sps-textarea--short"
          value={node.description}
          onChange={(e) => onPatch(node.id, { description: e.target.value })}
          onBlur={() => onMetaBlur(node.id)}
          placeholder="Brief summary..."
        />
      </div>
      {!isRoot ? (
        <ChildConditionField
          value={node.loadCondition}
          onChange={(v) => onPatch(node.id, { loadCondition: v })}
          onBlur={() => onMetaBlur(node.id)}
        />
      ) : null}
      <DefaultEnabledToggle
        checked={node.defaultEnabled}
        disabled={node.alwaysOn}
        alwaysOn={node.alwaysOn}
        isRoot={isRoot}
        onChange={(checked) => onPatch(node.id, { defaultEnabled: checked })}
      />
      <div className="sps-field">
        <label className="sps-label">AI prompt content</label>
        <textarea
          className="sps-textarea sps-textarea--content"
          value={node.content}
          onChange={(e) => onPatch(node.id, { content: e.target.value })}
          onBlur={(e) => onContentSave(node.id, e.target.value)}
          placeholder="Enter full AI instructions..."
        />
      </div>
      <div className="sps-delete-row">
        <button
          type="button"
          className={`sps-delete-btn${confirmDelete ? " is-confirm" : ""}`}
          onClick={() => onDelete(node.id)}
        >
          <Icons.Trash className="sps-icon-sm" />
          {confirmDelete ? "Click again to confirm" : "Delete node"}
        </button>
      </div>
    </div>
  );
}
