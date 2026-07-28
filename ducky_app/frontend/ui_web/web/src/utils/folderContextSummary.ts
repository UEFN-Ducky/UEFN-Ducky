import type { FolderItem } from "../types/panel";

export type FolderContextAgent = {
  id: string;
  name: string;
  duckyStyle?: string;
  model?: string;
  codingAgent?: string;
  contextTokens: number;
};

export type FolderContextSubgroup = {
  id: string;
  name: string;
  contextTokens: number;
  agentCount: number;
  /** Direct agents (+ nested leaf agents) for the right-side detail flyout. */
  agents: FolderContextAgent[];
};

export type FolderContextSummary = {
  name: string;
  isGroup: boolean;
  agents: FolderContextAgent[];
  subgroups: FolderContextSubgroup[];
  totalTokens: number;
  agentCount: number;
};

function flattenAgents(summary: FolderContextSummary): FolderContextAgent[] {
  return [
    ...summary.agents,
    ...summary.subgroups.flatMap((g) => g.agents),
  ];
}

/** Roll up agents + nested subgroup totals for a sidebar folder/group hover card. */
export function summarizeFolderContext(folder: FolderItem): FolderContextSummary {
  const agents: FolderContextAgent[] = folder.chats.map((c) => ({
    id: c.id,
    name: c.name?.trim() || "Ducky",
    duckyStyle: c.duckyStyle,
    model: (c.model || "").trim() || undefined,
    codingAgent: (c.codingAgent || "").trim() || undefined,
    contextTokens: Math.max(0, Number(c.contextTokens) || 0),
  }));
  const subgroups: FolderContextSubgroup[] = folder.children.map((child) => {
    const nested = summarizeFolderContext(child);
    return {
      id: child.id,
      name: child.name?.trim() || "Group",
      contextTokens: nested.totalTokens,
      agentCount: nested.agentCount,
      agents: flattenAgents(nested),
    };
  });
  const directTokens = agents.reduce((sum, a) => sum + a.contextTokens, 0);
  const subTokens = subgroups.reduce((sum, g) => sum + g.contextTokens, 0);
  const agentCount = agents.length + subgroups.reduce((sum, g) => sum + g.agentCount, 0);
  return {
    name: folder.name?.trim() || (folder.groupHubId ? "Group" : "Folder"),
    isGroup: Boolean(folder.groupHubId),
    agents,
    subgroups,
    totalTokens: directTokens + subTokens,
    agentCount,
  };
}

export function estimateFolderHoverCardHeight(summary: FolderContextSummary): number {
  const rows = summary.agents.length + summary.subgroups.length;
  return Math.min(360, 88 + rows * 24 + 40);
}

/** Find the sidebar folder whose linked group hub id matches. */
export function findFolderByHubId(
  folders: FolderItem[],
  hubId: string,
): FolderItem | null {
  const id = (hubId || "").trim();
  if (!id) return null;
  for (const folder of folders) {
    if ((folder.groupHubId || "").trim() === id) return folder;
    const nested = findFolderByHubId(folder.children, id);
    if (nested) return nested;
  }
  return null;
}
