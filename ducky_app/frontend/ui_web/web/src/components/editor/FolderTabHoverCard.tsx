import { useMemo, useState, type ReactNode } from "react";

import { DuckyAvatar } from "../ducky/DuckyAvatars";
import { aiTypeLabel } from "../groupMemberHover";
import { Icons } from "../../icons/Icons";
import type { EditorTabHoverCardPlacement } from "../../hooks/useEditorTabHoverCard";
import type { FolderItem } from "../../types/panel";
import { fmtCompactTokens } from "../../utils/contextFormat";
import {
  estimateFolderHoverCardHeight,
  summarizeFolderContext,
  type FolderContextAgent,
  type FolderContextSubgroup,
} from "../../utils/folderContextSummary";
import { EditorTabHoverCardShell } from "./EditorTabHoverCardShell";

interface FolderTabHoverCardProps {
  folder: FolderItem;
  disabled?: boolean;
  placement?: EditorTabHoverCardPlacement;
  children: ReactNode;
}

function AgentRows({
  agents,
  onEnter,
}: {
  agents: FolderContextAgent[];
  onEnter?: () => void;
}) {
  return (
    <>
      {agents.map((a) => (
        <div
          key={`a:${a.id}`}
          className="editor-tab-hover-card-folder-row"
          onMouseEnter={onEnter}
        >
          <span className="editor-tab-hover-card-folder-row-avatar">
            <DuckyAvatar styleId={a.duckyStyle} size={18} title={a.name} />
          </span>
          <span className="editor-tab-hover-card-folder-row-name" title={a.name}>
            {a.name}
          </span>
          <span
            className="editor-tab-hover-card-folder-row-model"
            title={aiTypeLabel(a.model, a.codingAgent)}
          >
            {aiTypeLabel(a.model, a.codingAgent)}
          </span>
          <span className="editor-tab-hover-card-folder-row-tokens">
            {fmtCompactTokens(a.contextTokens)}
          </span>
        </div>
      ))}
    </>
  );
}

function SubgroupFlyout({ group }: { group: FolderContextSubgroup }) {
  const agentLabel = `${group.agentCount} ${group.agentCount === 1 ? "agent" : "agents"}`;
  return (
    <div className="editor-tab-hover-card-subgroup-flyout" role="tooltip">
      <div className="editor-tab-hover-card-header">
        <div className="editor-tab-hover-card-icon editor-tab-hover-card-icon--group">
          <Icons.Users />
        </div>
        <div className="editor-tab-hover-card-titles">
          <div className="editor-tab-hover-card-name">{group.name}</div>
          <div className="editor-tab-hover-card-subtitle">{agentLabel}</div>
        </div>
      </div>
      {group.agents.length > 0 ? (
        <div className="editor-tab-hover-card-folder-list">
          <AgentRows agents={group.agents} />
        </div>
      ) : (
        <div className="editor-tab-hover-card-personality">No agents in this group yet.</div>
      )}
      <div className="editor-tab-hover-card-folder-total">
        <span>Total context</span>
        <span className="editor-tab-hover-card-folder-total-value">
          {fmtCompactTokens(group.contextTokens)} tokens
        </span>
      </div>
    </div>
  );
}

export function FolderTabHoverCard({
  folder,
  disabled = false,
  placement = "below",
  children,
}: FolderTabHoverCardProps) {
  const summary = useMemo(() => summarizeFolderContext(folder), [folder]);
  const cardHeight = estimateFolderHoverCardHeight(summary);
  const agentLabel = `${summary.agentCount} ${summary.agentCount === 1 ? "agent" : "agents"}`;
  const [flyoutId, setFlyoutId] = useState<string | null>(null);
  const flyout = summary.subgroups.find((g) => g.id === flyoutId) ?? null;
  // Sidebar on the right flips the main card left — flyout goes the other way.
  const flyoutSide = placement === "left" ? "left" : "right";

  return (
    <EditorTabHoverCardShell
      disabled={disabled}
      placement={placement}
      cardHeight={cardHeight}
      card={
        <div
          className={`editor-tab-hover-card-folder-body editor-tab-hover-card-folder-body--flyout-${flyoutSide}`}
          onMouseLeave={() => setFlyoutId(null)}
        >
          <div className="editor-tab-hover-card-header">
            <div
              className={`editor-tab-hover-card-icon${summary.isGroup ? " editor-tab-hover-card-icon--group" : ""}`}
            >
              {summary.isGroup ? <Icons.Users /> : <Icons.Folder />}
            </div>
            <div className="editor-tab-hover-card-titles">
              <div className="editor-tab-hover-card-name">{summary.name}</div>
              <div className="editor-tab-hover-card-subtitle">{agentLabel}</div>
            </div>
          </div>

          {summary.subgroups.length > 0 || summary.agents.length > 0 ? (
            <div className="editor-tab-hover-card-folder-list">
              {summary.subgroups.map((g) => (
                <div
                  key={`g:${g.id}`}
                  className={`editor-tab-hover-card-folder-row editor-tab-hover-card-folder-row--subgroup${
                    flyoutId === g.id ? " is-flyout-open" : ""
                  }`}
                  onMouseEnter={() => setFlyoutId(g.id)}
                >
                  <span className="editor-tab-hover-card-folder-row-icon" aria-hidden="true">
                    <Icons.Users />
                  </span>
                  <span className="editor-tab-hover-card-folder-row-name">
                    {g.name}
                    <span className="editor-tab-hover-card-folder-row-meta">
                      {" "}
                      · {g.agentCount}
                    </span>
                  </span>
                  <span className="editor-tab-hover-card-folder-row-tokens">
                    {fmtCompactTokens(g.contextTokens)}
                  </span>
                </div>
              ))}
              <AgentRows agents={summary.agents} onEnter={() => setFlyoutId(null)} />
            </div>
          ) : (
            <div className="editor-tab-hover-card-personality">No agents in this group yet.</div>
          )}

          <div className="editor-tab-hover-card-folder-total">
            <span>Total context</span>
            <span className="editor-tab-hover-card-folder-total-value">
              {fmtCompactTokens(summary.totalTokens)} tokens
            </span>
          </div>

          {flyout ? <SubgroupFlyout group={flyout} /> : null}
        </div>
      }
    >
      {children}
    </EditorTabHoverCardShell>
  );
}
