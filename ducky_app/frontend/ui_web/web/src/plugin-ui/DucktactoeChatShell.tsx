import { useCallback, useEffect, useRef, useState } from "react";
import { ChatPane } from "../components/ChatPane";
import { ChatColumnResizeHandle } from "../components/ChatColumnResizeHandle";
import { ErrorBoundary } from "../components/ErrorBoundary";
import type { ChatTab, FolderItem } from "../types/panel";
import { DucktactoeBoardAside } from "./DucktactoeBoardAside";
import { subscribeDucktactoeBoardPings } from "./ducktactoePings";
import "./plugin-ui.css";

const BOARD_WIDTH_KEY = "uefn-ducktactoe-board-width";
const DEFAULT_BOARD_WIDTH = 340;
const MIN_BOARD_WIDTH = 220;
const MAX_BOARD_WIDTH = 560;
const MIN_CHAT_WIDTH = 280;

function readBoardWidth(): number {
  try {
    const raw = localStorage.getItem(BOARD_WIDTH_KEY);
    const n = raw == null ? DEFAULT_BOARD_WIDTH : Number(raw);
    if (!Number.isFinite(n)) return DEFAULT_BOARD_WIDTH;
    return Math.min(MAX_BOARD_WIDTH, Math.max(MIN_BOARD_WIDTH, Math.round(n)));
  } catch {
    return DEFAULT_BOARD_WIDTH;
  }
}

function persistBoardWidth(width: number): void {
  try {
    localStorage.setItem(BOARD_WIDTH_KEY, String(width));
  } catch {
    /* ignore */
  }
}

export type DucktactoeChatShellProps = {
  chat: ChatTab;
  allChats: ChatTab[];
  folders?: FolderItem[];
  contextFilePath?: string | null;
  runningChatIds: Set<string>;
  variant?: "default" | "focus" | "popup";
  onOpenChat: (chat: ChatTab) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  onOpenPlan?: (chatId: string, title?: string) => void;
  onDismissChatAlert?: (chatId: string) => void;
};

/**
 * Chat-first Duck-Tac-Toe: collapsible board aside + ChatPane (messages + Live).
 */
export function DucktactoeChatShell({
  chat,
  allChats,
  folders,
  contextFilePath,
  runningChatIds,
  variant = "default",
  onOpenChat,
  onOpenFile,
  onOpenPlan,
  onDismissChatAlert,
}: DucktactoeChatShellProps) {
  const [boardCollapsed, setBoardCollapsed] = useState(false);
  const [boardWidth, setBoardWidth] = useState(readBoardWidth);
  const shellRef = useRef<HTMLDivElement>(null);
  const boardWidthRef = useRef(boardWidth);
  const runningRef = useRef(false);
  boardWidthRef.current = boardWidth;

  useEffect(() => {
    runningRef.current = runningChatIds.has(chat.id);
  }, [chat.id, runningChatIds]);

  useEffect(() => {
    return subscribeDucktactoeBoardPings({
      chat,
      isRunning: () => runningRef.current,
    });
  }, [chat]);

  const clampBoardWidth = useCallback((next: number) => {
    const shellW = shellRef.current?.clientWidth || 0;
    const maxByShell =
      shellW > 0 ? Math.max(MIN_BOARD_WIDTH, shellW - MIN_CHAT_WIDTH) : MAX_BOARD_WIDTH;
    const max = Math.min(MAX_BOARD_WIDTH, maxByShell);
    return Math.min(max, Math.max(MIN_BOARD_WIDTH, Math.round(next)));
  }, []);

  const onBoardDrag = useCallback(
    (deltaX: number) => {
      const next = clampBoardWidth(boardWidthRef.current + deltaX);
      boardWidthRef.current = next;
      setBoardWidth(next);
    },
    [clampBoardWidth],
  );

  const onBoardDragEnd = useCallback(() => {
    persistBoardWidth(boardWidthRef.current);
  }, []);

  return (
    <div
      ref={shellRef}
      className={`ducktactoe-chat-shell${boardCollapsed ? " ducktactoe-chat-shell--board-collapsed" : ""}`}
    >
      <DucktactoeBoardAside
        hostId={chat.id}
        collapsed={boardCollapsed}
        widthPx={boardCollapsed ? undefined : boardWidth}
        onToggle={() => setBoardCollapsed((v) => !v)}
      />
      {!boardCollapsed ? (
        <div className="ducktactoe-chat-shell__splitter">
          <ChatColumnResizeHandle
            side="left"
            label="Resize board"
            onDrag={onBoardDrag}
            onDragEnd={onBoardDragEnd}
          />
        </div>
      ) : null}
      <div className="ducktactoe-chat-shell__chat">
        <ErrorBoundary label="Duck-Tac-Toe chat" resetKeys={[chat.id]}>
          <ChatPane
            key={chat.id}
            chat={chat}
            visible
            variant={variant}
            allChats={allChats}
            folders={folders}
            contextFilePath={contextFilePath ?? undefined}
            onOpenChat={onOpenChat}
            onOpenFile={onOpenFile}
            onOpenPlan={onOpenPlan}
            isAgentRunning={runningChatIds.has(chat.id)}
            onEngage={onDismissChatAlert ? () => onDismissChatAlert(chat.id) : undefined}
          />
        </ErrorBoundary>
      </div>
    </div>
  );
}
