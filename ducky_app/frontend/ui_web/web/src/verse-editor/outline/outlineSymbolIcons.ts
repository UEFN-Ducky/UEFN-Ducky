/** LSP SymbolKind (1-based) → VS Code codicon class name. */
const LSP_KIND_TO_CODICON: Record<number, string> = {
  1: "codicon-symbol-file",
  2: "codicon-symbol-module",
  3: "codicon-symbol-namespace",
  4: "codicon-symbol-package",
  5: "codicon-symbol-class",
  6: "codicon-symbol-method",
  7: "codicon-symbol-property",
  8: "codicon-symbol-field",
  9: "codicon-symbol-constructor",
  10: "codicon-symbol-enum",
  11: "codicon-symbol-interface",
  12: "codicon-symbol-function",
  13: "codicon-symbol-variable",
  14: "codicon-symbol-constant",
  15: "codicon-symbol-string",
  16: "codicon-symbol-number",
  17: "codicon-symbol-boolean",
  18: "codicon-symbol-array",
  19: "codicon-symbol-object",
  20: "codicon-symbol-key",
  21: "codicon-symbol-null",
  22: "codicon-symbol-enum-member",
  23: "codicon-symbol-struct",
  24: "codicon-symbol-event",
  25: "codicon-symbol-operator",
  26: "codicon-symbol-type-parameter",
};

export function lspKindToCodicon(kind: number): string {
  return LSP_KIND_TO_CODICON[kind] ?? "codicon-symbol-property";
}

/** Common Verse / LSP symbol kinds shown in the outline filter dropdown. */
export const OUTLINE_KIND_OPTIONS = [
  { kind: 5, label: "Classes" },
  { kind: 23, label: "Structs" },
  { kind: 12, label: "Functions" },
  { kind: 6, label: "Methods" },
  { kind: 13, label: "Variables" },
  { kind: 8, label: "Fields" },
  { kind: 10, label: "Enums" },
  { kind: 14, label: "Constants" },
] as const;

/** Keep nodes that match `kinds` or have a matching descendant (ancestors stay visible). */
export function filterOutlineByKinds(
  nodes: OutlineNode[],
  kinds: ReadonlySet<number> | null | undefined,
): OutlineNode[] {
  if (!kinds || kinds.size === 0) return nodes;

  const filterNode = (node: OutlineNode): OutlineNode | null => {
    const filteredChildren = node.children
      .map(filterNode)
      .filter((child): child is OutlineNode => child !== null);
    if (kinds.has(node.kind) || filteredChildren.length > 0) {
      return { ...node, children: filteredChildren };
    }
    return null;
  };

  return nodes.map(filterNode).filter((node): node is OutlineNode => node !== null);
}

export type OutlineNode = {
  name: string;
  kind: number;
  line: number;
  endLine: number;
  children: OutlineNode[];
};

type RawSymbol = {
  name: string;
  kind: number;
  line: number;
  endLine: number;
  children: RawSymbol[];
};

function parseRawSymbols(raw: unknown): RawSymbol[] {
  if (!Array.isArray(raw)) return [];

  return raw
    .map((sym) => {
      if (!sym || typeof sym !== "object") return null;
      const s = sym as {
        name?: string;
        kind?: number;
        range?: { start?: { line?: number }; end?: { line?: number } };
        location?: { range?: { start?: { line?: number }; end?: { line?: number } } };
        children?: unknown[];
      };
      const name = typeof s.name === "string" ? s.name : "";
      if (!name) return null;

      const startLine =
        (typeof s.range?.start?.line === "number" ? s.range.start.line : undefined) ??
        (typeof s.location?.range?.start?.line === "number" ? s.location.range.start.line : undefined) ??
        0;
      const endLine =
        (typeof s.range?.end?.line === "number" ? s.range.end.line : undefined) ??
        (typeof s.location?.range?.end?.line === "number" ? s.location.range.end.line : undefined) ??
        startLine;

      const children = Array.isArray(s.children) ? parseRawSymbols(s.children) : [];

      return {
        name,
        kind: typeof s.kind === "number" ? s.kind : 0,
        line: startLine + 1,
        endLine: endLine + 1,
        children,
      };
    })
    .filter((n): n is RawSymbol => n !== null);
}

function toOutlineNode(sym: RawSymbol): OutlineNode {
  return {
    name: sym.name,
    kind: sym.kind,
    line: sym.line,
    endLine: sym.endLine,
    children: sym.children.map(toOutlineNode),
  };
}

function nestFlatSymbols(flat: RawSymbol[]): OutlineNode[] {
  const sorted = [...flat].sort((a, b) => a.line - b.line || a.endLine - b.endLine);
  const roots: OutlineNode[] = [];
  const stack: { node: OutlineNode; endLine: number }[] = [];

  for (const sym of sorted) {
    while (stack.length && sym.line > stack[stack.length - 1].endLine) {
      stack.pop();
    }

    const node: OutlineNode = {
      name: sym.name,
      kind: sym.kind,
      line: sym.line,
      endLine: sym.endLine,
      children: sym.children.length ? sym.children.map(toOutlineNode) : [],
    };

    if (stack.length) {
      stack[stack.length - 1].node.children.push(node);
    } else {
      roots.push(node);
    }

    stack.push({ node, endLine: sym.endLine });
  }

  return roots;
}

export type FlatOutlineSymbol = {
  name: string;
  kind: number;
  line: number;
  breadcrumb?: string;
};

export function flattenOutlineSymbols(nodes: OutlineNode[], breadcrumb = ""): FlatOutlineSymbol[] {
  const out: FlatOutlineSymbol[] = [];
  for (const node of nodes) {
    const nextCrumb = breadcrumb ? `${breadcrumb} › ${node.name}` : node.name;
    out.push({
      name: node.name,
      kind: node.kind,
      line: node.line,
      breadcrumb: breadcrumb || undefined,
    });
    if (node.children.length) {
      out.push(...flattenOutlineSymbols(node.children, nextCrumb));
    }
  }
  return out;
}

export function buildOutlineTree(raw: unknown): OutlineNode[] {
  const parsed = parseRawSymbols(raw);
  if (!parsed.length) return [];

  const hasHierarchy = parsed.some((sym) => sym.children.length > 0);
  if (hasHierarchy) {
    return parsed.map(toOutlineNode);
  }

  return nestFlatSymbols(parsed);
}

/**
 * Node ids intentionally exclude line numbers — inserting a line above a symbol
 * must not change its id, or expansion/selection state resets on every edit.
 */
export function outlineNodeId(parentId: string, index: number, node: OutlineNode): string {
  return `${parentId}/${index}:${node.name}`;
}

export function collectDefaultExpanded(nodes: OutlineNode[], prefix = "root"): Set<string> {
  const keys = new Set<string>();
  const walk = (items: OutlineNode[], parentPrefix: string) => {
    items.forEach((node, index) => {
      if (!node.children.length) return;
      const id = outlineNodeId(parentPrefix, index, node);
      keys.add(id);
      walk(node.children, id);
    });
  };
  walk(nodes, prefix);
  return keys;
}

export function collectAllExpandable(nodes: OutlineNode[], prefix = "root"): Set<string> {
  const keys = new Set<string>();
  const walk = (items: OutlineNode[], parentPrefix: string) => {
    items.forEach((node, index) => {
      const id = outlineNodeId(parentPrefix, index, node);
      if (node.children.length) {
        keys.add(id);
        walk(node.children, id);
      }
    });
  };
  walk(nodes, prefix);
  return keys;
}

/**
 * Structure-only signature (no line numbers): edits that merely shift lines keep
 * the signature stable, so the outline preserves expansion instead of resetting.
 */
export function outlineTreeSignature(nodes: OutlineNode[]): string {
  const parts: string[] = [];
  const walk = (items: OutlineNode[], depth: number) => {
    for (const node of items) {
      parts.push(`${depth}:${node.kind}:${node.name}`);
      if (node.children.length) walk(node.children, depth + 1);
    }
  };
  walk(nodes, 0);
  return parts.join("|");
}

export type OutlineRow = {
  id: string;
  node: OutlineNode;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
};

/** Flatten only the visible (expanded) part of the tree for windowed rendering. */
export function flattenExpandedRows(nodes: OutlineNode[], expandedKeys: Set<string>): OutlineRow[] {
  const out: OutlineRow[] = [];
  const walk = (items: OutlineNode[], parentId: string, depth: number) => {
    items.forEach((node, index) => {
      const id = outlineNodeId(parentId, index, node);
      const hasChildren = node.children.length > 0;
      const expanded = hasChildren && expandedKeys.has(id);
      out.push({ id, node, depth, hasChildren, expanded });
      if (expanded) walk(node.children, id, depth + 1);
    });
  };
  walk(nodes, "root", 0);
  return out;
}

/** Find the deepest node whose range contains `line`, and return its id + ancestor ids. */
export function findActiveSymbolPath(
  nodes: OutlineNode[],
  line: number,
  prefix = "root",
): { activeId: string | null; ancestorIds: string[] } {
  let activeId: string | null = null;
  const ancestorIds: string[] = [];

  const walk = (items: OutlineNode[], parentPrefix: string): boolean => {
    for (let index = 0; index < items.length; index++) {
      const node = items[index];
      const id = outlineNodeId(parentPrefix, index, node);
      if (line >= node.line && line <= node.endLine) {
        activeId = id;
        ancestorIds.push(id);
        if (node.children.length) {
          walk(node.children, id);
        }
        return true;
      }
    }
    return false;
  };

  walk(nodes, prefix);
  return { activeId, ancestorIds };
}
