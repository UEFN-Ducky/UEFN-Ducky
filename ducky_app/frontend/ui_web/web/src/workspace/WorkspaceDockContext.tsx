import { createContext, useContext, type ReactNode } from "react";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import { useWorkspaceDockLayout, type WorkspaceDockLayoutApi } from "../hooks/useWorkspaceDockLayout";

const WorkspaceDockContext = createContext<WorkspaceDockLayoutApi | null>(null);

export function WorkspaceDockProvider({
  children,
  windowId = WINDOW_ID,
}: {
  children: ReactNode;
  windowId?: string;
}) {
  const dock = useWorkspaceDockLayout(windowId);
  return <WorkspaceDockContext.Provider value={dock}>{children}</WorkspaceDockContext.Provider>;
}

export function useWorkspaceDock(): WorkspaceDockLayoutApi {
  const ctx = useContext(WorkspaceDockContext);
  if (!ctx) throw new Error("useWorkspaceDock requires WorkspaceDockProvider");
  return ctx;
}

export function useWorkspaceDockOptional(): WorkspaceDockLayoutApi | null {
  return useContext(WorkspaceDockContext);
}
