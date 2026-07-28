import type { ReactNode, ComponentType } from "react";
import type { OpenFileHandler } from "../../types/richContent";

export interface ToolCardBodyProps {
  toolName: string;
  args: Record<string, unknown>;
  argsText: string;
  resultText: string;
  isSuccess: boolean;
  isError: boolean;
  /** When false, only arguments (and running chrome) are shown. */
  showResult?: boolean;
  hideArgs?: boolean;
  hint?: string;
  onOpenFile?: OpenFileHandler;
}

export interface ToolCategory {
  /** Drives `tool-execution-card-shell--cat-{id}` and CSS accent. */
  id: string;
  label?: (name: string) => string;
  icon: () => ReactNode;
  match: (toolName: string) => boolean;
  /** Omit to use DefaultBody. */
  Body?: ComponentType<ToolCardBodyProps>;
}
