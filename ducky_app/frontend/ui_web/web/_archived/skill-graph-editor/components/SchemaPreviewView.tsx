import type { GraphNode } from "../model/graphTypes";

interface SchemaPreviewViewProps {
  packLabel: string;
  nodes: GraphNode[];
}

export function SchemaPreviewView({ packLabel, nodes }: SchemaPreviewViewProps) {
  const tree = nodes.map((n) => ({
    id: n.id,
    parent_id: n.parentId,
    label: n.title,
    description: n.description,
    default_enabled: n.defaultEnabled,
    always_on: n.alwaysOn,
    load_condition: n.loadCondition || null,
    x: n.x,
    y: n.y,
  }));

  return (
    <div className="sps-schema-view">
      <h3 className="sps-schema-title">Pack schema — {packLabel}</h3>
      <p className="sps-schema-desc">Debug view of the active pack tree (not the portable export file).</p>
      <pre className="sps-schema-pre">{JSON.stringify(tree, null, 2)}</pre>
    </div>
  );
}
