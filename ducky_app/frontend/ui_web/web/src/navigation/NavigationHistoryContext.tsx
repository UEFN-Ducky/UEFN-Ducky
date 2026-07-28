import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ViewId } from "../types/panel";
import {
  applySettingsHistory,
  sameSettingsLocation,
  type SettingsNavLocation,
} from "./settingsHistory";

/** A place the user can navigate back/forward to (VS Code-style history). */
export type NavLocation =
  | { kind: "file"; path: string; name: string }
  | { kind: "chat"; chatId: string; name: string }
  | { kind: "view"; view: Extract<ViewId, "settings"> }
  | SettingsNavLocation;

export function sameLocation(a: NavLocation, b: NavLocation): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === "file" && b.kind === "file") return a.path === b.path;
  if (a.kind === "chat" && b.kind === "chat") return a.chatId === b.chatId;
  if (a.kind === "view" && b.kind === "view") return a.view === b.view;
  if (a.kind === "settings" && b.kind === "settings") return sameSettingsLocation(a, b);
  return false;
}

const MAX_HISTORY = 100;

export interface NavigationHistoryValue {
  canBack: boolean;
  canForward: boolean;
  back: () => void;
  forward: () => void;
  /** Record a visited location. Re-recording the current spot is a no-op, so this is
   * safe to call from an effect on every render. */
  record: (loc: NavLocation) => void;
  /** Entry at the current index (null when the stack is empty). */
  peekCurrent: () => NavLocation | null;
  /** Entry one step back (null when canBack is false). */
  peekBack: () => NavLocation | null;
  /** Overwrite the current stack entry without applying it (UI already updated). */
  replace: (loc: NavLocation) => void;
  /** App-level: switch the top-level view (chat/settings) when a location is applied. */
  registerViewApplier: (fn: ((view: ViewId) => void) | null) => void;
  /** ChatView-level: open/focus the tab for a file/chat/settings location. */
  registerOpenApplier: (fn: ((loc: NavLocation) => void) | null) => void;
}

const NavigationHistoryContext = createContext<NavigationHistoryValue | null>(null);

export function NavigationHistoryProvider({ children }: { children: ReactNode }) {
  const stackRef = useRef<NavLocation[]>([]);
  const indexRef = useRef(-1);
  const viewApplierRef = useRef<((view: ViewId) => void) | null>(null);
  const openApplierRef = useRef<((loc: NavLocation) => void) | null>(null);
  const [flags, setFlags] = useState({ canBack: false, canForward: false });

  const syncFlags = useCallback(() => {
    setFlags((prev) => {
      const canBack = indexRef.current > 0;
      const canForward = indexRef.current < stackRef.current.length - 1;
      return prev.canBack === canBack && prev.canForward === canForward
        ? prev
        : { canBack, canForward };
    });
  }, []);

  const record = useCallback(
    (loc: NavLocation) => {
      const stack = stackRef.current;
      const current = indexRef.current >= 0 ? stack[indexRef.current] : null;
      // Applying a back/forward step re-emits the same location — dedupe so it doesn't
      // truncate the forward stack we just moved into.
      if (current && sameLocation(current, loc)) return;
      stack.splice(indexRef.current + 1); // drop any forward entries
      stack.push(loc);
      if (stack.length > MAX_HISTORY) stack.splice(0, stack.length - MAX_HISTORY);
      indexRef.current = stack.length - 1;
      syncFlags();
    },
    [syncFlags],
  );

  const peekCurrent = useCallback((): NavLocation | null => {
    const idx = indexRef.current;
    return idx >= 0 ? stackRef.current[idx] ?? null : null;
  }, []);

  const peekBack = useCallback((): NavLocation | null => {
    const idx = indexRef.current;
    return idx > 0 ? stackRef.current[idx - 1] ?? null : null;
  }, []);

  const replace = useCallback(
    (loc: NavLocation) => {
      const idx = indexRef.current;
      if (idx < 0) {
        record(loc);
        return;
      }
      stackRef.current[idx] = loc;
      syncFlags();
    },
    [record, syncFlags],
  );

  const applyAt = useCallback(
    (idx: number) => {
      const loc = stackRef.current[idx];
      if (!loc) return;
      indexRef.current = idx;
      if (loc.kind === "view") {
        viewApplierRef.current?.(loc.view);
      } else if (loc.kind === "settings") {
        // Welcome overlay: flip App view. Project mode: ChatView opens the Settings editor tab.
        viewApplierRef.current?.("settings");
        openApplierRef.current?.(loc);
        applySettingsHistory(loc);
      } else {
        viewApplierRef.current?.("chat");
        openApplierRef.current?.(loc);
      }
      syncFlags();
    },
    [syncFlags],
  );

  const back = useCallback(() => {
    if (indexRef.current > 0) applyAt(indexRef.current - 1);
  }, [applyAt]);

  const forward = useCallback(() => {
    if (indexRef.current < stackRef.current.length - 1) applyAt(indexRef.current + 1);
  }, [applyAt]);

  const registerViewApplier = useCallback((fn: ((view: ViewId) => void) | null) => {
    viewApplierRef.current = fn;
  }, []);
  const registerOpenApplier = useCallback((fn: ((loc: NavLocation) => void) | null) => {
    openApplierRef.current = fn;
  }, []);

  const value = useMemo<NavigationHistoryValue>(
    () => ({
      canBack: flags.canBack,
      canForward: flags.canForward,
      back,
      forward,
      record,
      peekCurrent,
      peekBack,
      replace,
      registerViewApplier,
      registerOpenApplier,
    }),
    [
      flags.canBack,
      flags.canForward,
      back,
      forward,
      record,
      peekCurrent,
      peekBack,
      replace,
      registerViewApplier,
      registerOpenApplier,
    ],
  );

  return (
    <NavigationHistoryContext.Provider value={value}>{children}</NavigationHistoryContext.Provider>
  );
}

/** Non-throwing — returns null outside a provider (e.g. the shared Header in focus windows). */
export function useNavigationHistoryOptional(): NavigationHistoryValue | null {
  return useContext(NavigationHistoryContext);
}
