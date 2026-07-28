import { createContext, createElement, useCallback, useContext, useState, type ReactNode } from "react";

/** Preserves expanded tool/message sections across chat tab remounts. */
const collapseStore = new Map<string, boolean>();

export function chatCollapseKey(scope: string, ...parts: string[]): string {
  return [scope, ...parts].join(":");
}

function readCollapse(key: string): boolean | undefined {
  return collapseStore.get(key);
}

function writeCollapse(key: string, open: boolean): void {
  collapseStore.set(key, open);
}

export function useChatCollapseState(
  key: string,
  defaultOpen: boolean,
): [boolean, (next: boolean | ((prev: boolean) => boolean)) => void] {
  const [open, setOpenState] = useState(() => readCollapse(key) ?? defaultOpen);

  const setOpen = useCallback(
    (value: boolean | ((prev: boolean) => boolean)) => {
      setOpenState((prev) => {
        const next = typeof value === "function" ? value(prev) : value;
        writeCollapse(key, next);
        return next;
      });
    },
    [key],
  );

  return [open, setOpen];
}

export function readChatCollapseFlag(key: string): boolean {
  return readCollapse(key) ?? false;
}

export function writeChatCollapseFlag(key: string, value: boolean): void {
  writeCollapse(key, value);
}

const ChatCollapseScopeContext = createContext("");

export function ChatCollapseScopeProvider({
  scope,
  children,
}: {
  scope: string;
  children: ReactNode;
}) {
  return createElement(ChatCollapseScopeContext.Provider, { value: scope }, children);
}

export function useChatCollapseScope(): string {
  return useContext(ChatCollapseScopeContext);
}
