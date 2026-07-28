export type StudioView = "packs" | "graph" | "schema";

export interface LayoutPos {
  x: number;
  y: number;
}

export interface GraphNode {
  id: string;
  parentId: string | null;
  type: "skill";
  title: string;
  description: string;
  loadCondition: string;
  content: string;
  defaultEnabled: boolean;
  alwaysOn: boolean;
  x: number;
  y: number;
}

export interface PackSummary {
  id: string;
  label: string;
  description: string;
  kind: string;
}

export interface PackGraph {
  packId: string;
  label: string;
  description: string;
  kind: string;
  nodes: Record<string, GraphNode>;
}
