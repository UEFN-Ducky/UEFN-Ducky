import type { GraphNode } from "../model/graphTypes";
import type { SpsCanvasTheme } from "../utils/canvasTheme";
import {
  COLLAPSED_HEIGHT,
  EXPANDED_SKILL_HEIGHT,
  GRID_SIZE,
  HEADER_HEIGHT,
  NODE_WIDTH,
} from "../constants/layout";
import { PATHS } from "./icons";

export type HitRegionType = "header" | "toggle" | "add";

export interface HitRegion {
  type: HitRegionType;
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface DrawGraphArgs {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  dpr: number;
  nodes: GraphNode[];
  expandedNodes: Record<string, boolean>;
  dragNodeId: string | null;
  pan: { x: number; y: number };
  zoom: number;
  hitRegions: HitRegion[];
  theme: SpsCanvasTheme;
}

function drawIcon(
  ctx: CanvasRenderingContext2D,
  path: string,
  x: number,
  y: number,
  color: string,
  scale = 1,
  strokeWidth = 2,
) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.strokeStyle = color;
  ctx.lineWidth = strokeWidth;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke(new Path2D(path));
  ctx.restore();
}

function drawText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  font: string,
  color: string,
  maxWidth?: number,
) {
  ctx.font = font;
  ctx.fillStyle = color;
  let txt = text || "";
  if (maxWidth) {
    if (ctx.measureText(txt).width > maxWidth) {
      while (ctx.measureText(`${txt}...`).width > maxWidth && txt.length > 0) {
        txt = txt.slice(0, -1);
      }
      txt += "...";
    }
  }
  ctx.fillText(txt, x, y);
}

export function drawSkillGraph({
  ctx,
  width,
  height,
  dpr,
  nodes,
  expandedNodes,
  dragNodeId,
  pan,
  zoom,
  hitRegions,
  theme,
}: DrawGraphArgs) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = theme.bg;
  ctx.fillRect(0, 0, width, height);
  ctx.textBaseline = "top";
  hitRegions.length = 0;

  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  const offsetX = pan.x % (GRID_SIZE * zoom);
  const offsetY = pan.y % (GRID_SIZE * zoom);
  const gridStep = GRID_SIZE * zoom;
  ctx.beginPath();
  for (let x = offsetX; x < width; x += gridStep) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
  }
  for (let y = offsetY; y < height; y += gridStep) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.stroke();

  ctx.save();
  ctx.translate(pan.x, pan.y);
  ctx.scale(zoom, zoom);

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  for (const node of nodes) {
    if (!node.parentId) continue;
    const parent = nodeMap.get(node.parentId);
    if (!parent) continue;
    const startX = parent.x + NODE_WIDTH;
    const startY = parent.y + 32;
    const endX = node.x;
    const endY = node.y + 32;
    const cp1x = startX + (endX - startX) / 2;
    ctx.strokeStyle = theme.edge;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.bezierCurveTo(cp1x, startY, cp1x, endY, endX, endY);
    ctx.stroke();
  }

  for (const node of nodes) {
    const isExpanded = !!expandedNodes[node.id];
    const nodeHeight = isExpanded ? EXPANDED_SKILL_HEIGHT : COLLAPSED_HEIGHT;
    const isDragging = dragNodeId === node.id;
    const isDefaultOn = node.defaultEnabled || node.alwaysOn;

    ctx.save();
    if (isDragging) {
      ctx.shadowColor = theme.shadowActive;
      ctx.shadowBlur = 30;
      ctx.shadowOffsetY = 10;
      ctx.translate(0, -2);
    } else if (isDefaultOn) {
      ctx.shadowColor = theme.shadowDefault;
      ctx.shadowBlur = 18;
      ctx.shadowOffsetY = 8;
    } else {
      ctx.shadowColor = theme.shadow;
      ctx.shadowBlur = 20;
      ctx.shadowOffsetY = 10;
    }

    ctx.fillStyle = isDefaultOn ? theme.nodeFillDefault : theme.nodeFill;
    ctx.strokeStyle = isDragging
      ? theme.nodeStrokeActive
      : isDefaultOn
        ? theme.nodeStrokeDefault
        : isExpanded
          ? theme.nodeStrokeActive
          : theme.nodeStroke;
    ctx.lineWidth = isDragging || isExpanded || isDefaultOn ? 2 : 1;
    ctx.beginPath();
    ctx.roundRect(node.x, node.y, NODE_WIDTH, nodeHeight, 16);
    ctx.fill();
    ctx.stroke();
    ctx.restore();

    ctx.beginPath();
    ctx.strokeStyle = isDefaultOn ? theme.nodeStrokeDefault : theme.divider;
    ctx.moveTo(node.x, node.y + HEADER_HEIGHT);
    ctx.lineTo(node.x + NODE_WIDTH, node.y + HEADER_HEIGHT);
    ctx.stroke();

    ctx.fillStyle = theme.iconBg;
    ctx.beginPath();
    ctx.roundRect(node.x + 16, node.y + 14, 32, 32, 8);
    ctx.fill();
    drawIcon(ctx, PATHS.layers, node.x + 20, node.y + 18, theme.iconColor, 0.9, 2.5);

    drawText(ctx, node.title || "Untitled", node.x + 60, node.y + 16, "bold 14px sans-serif", theme.title, 200);
    const typeLabel = isDefaultOn ? "DEFAULT ON" : "SKILL";
    drawText(
      ctx,
      typeLabel,
      node.x + 60,
      node.y + 36,
      "11px sans-serif",
      isDefaultOn ? theme.iconColor : theme.muted,
    );

    if (isDefaultOn) {
      const badgeText = node.alwaysOn ? "ALWAYS ON" : "DEFAULT";
      const badgeW = ctx.measureText(badgeText).width + 16;
      ctx.fillStyle = theme.iconBg;
      ctx.strokeStyle = theme.nodeStrokeDefault;
      ctx.beginPath();
      ctx.roundRect(node.x + NODE_WIDTH - badgeW - 44, node.y + 14, badgeW, 20, 4);
      ctx.fill();
      ctx.stroke();
      drawText(ctx, badgeText, node.x + NODE_WIDTH - badgeW - 36, node.y + 17, "bold 9px sans-serif", theme.iconColor);
    }

    ctx.fillStyle = theme.btnBg;
    ctx.beginPath();
    ctx.roundRect(node.x + 300, node.y + 18, 24, 24, 6);
    ctx.fill();
    drawIcon(
      ctx,
      isExpanded ? PATHS.chevronUp : PATHS.chevronDown,
      node.x + 304,
      node.y + 22,
      theme.chevron,
      0.7,
    );

    if (!isExpanded) {
      drawText(
        ctx,
        node.description || "No description...",
        node.x + 16,
        node.y + 74,
        "12px sans-serif",
        theme.muted,
        NODE_WIDTH - 32,
      );
      if (node.loadCondition) {
        const badgeW = Math.min(ctx.measureText(node.loadCondition).width + 30, NODE_WIDTH - 32);
        ctx.fillStyle = theme.chipBg;
        ctx.strokeStyle = theme.chipStroke;
        ctx.beginPath();
        ctx.roundRect(node.x + 16, node.y + 100, badgeW, 24, 4);
        ctx.fill();
        ctx.stroke();
        drawIcon(ctx, PATHS.target, node.x + 22, node.y + 105, theme.iconColor, 0.6);
        drawText(
          ctx,
          node.loadCondition,
          node.x + 40,
          node.y + 105,
          "10px monospace",
          theme.iconColor,
          NODE_WIDTH - 64,
        );
      }
    }

    const btnY = node.y + nodeHeight / 2;
    ctx.fillStyle = theme.btnBg;
    ctx.strokeStyle = theme.btnStroke;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(node.x + NODE_WIDTH, btnY, 14, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    drawIcon(ctx, PATHS.plus, node.x + NODE_WIDTH - 8, btnY - 8, theme.chevron, 0.7);

    hitRegions.push({
      type: "header",
      id: node.id,
      x: node.x,
      y: node.y,
      w: NODE_WIDTH - 40,
      h: HEADER_HEIGHT,
    });
    hitRegions.push({
      type: "toggle",
      id: node.id,
      x: node.x + 290,
      y: node.y + 10,
      w: 40,
      h: 40,
    });
    hitRegions.push({
      type: "add",
      id: node.id,
      x: node.x + NODE_WIDTH - 20,
      y: btnY - 20,
      w: 40,
      h: 40,
    });
  }

  ctx.restore();
}
