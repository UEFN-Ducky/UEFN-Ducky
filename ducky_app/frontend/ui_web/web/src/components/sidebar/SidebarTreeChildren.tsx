import { createContext, useContext, type CSSProperties, type ReactNode, type Ref } from "react";

const TreeIndentDepthContext = createContext(0);

interface SidebarTreeChildrenProps {
  nestRef?: Ref<HTMLDivElement>;
  children: ReactNode;
}

/**
 * Nest wrapper that bumps tree indent one step per level.
 * Indent is set in px via inline style — Chromium/WebView2 treats
 * self-referential `--indent: calc(var(--indent) + step)` as a cycle
 * (invalid → 0), which flattened both Duckies and Content trees.
 */
export function SidebarTreeChildren({ nestRef, children }: SidebarTreeChildrenProps) {
  const depth = useContext(TreeIndentDepthContext) + 1;
  const style = {
    "--sidebar-tree-indent-level": `calc(${depth} * var(--sidebar-tree-step, 16px))`,
  } as CSSProperties;

  return (
    <TreeIndentDepthContext.Provider value={depth}>
      <div className="sidebar-tree-children-clip">
        <div ref={nestRef} className="sidebar-tree-children" style={style}>
          {children}
        </div>
      </div>
    </TreeIndentDepthContext.Provider>
  );
}
