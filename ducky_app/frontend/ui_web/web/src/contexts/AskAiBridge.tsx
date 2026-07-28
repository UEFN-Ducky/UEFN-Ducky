import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { AskAiHandlers } from "./askAiHandlersRef";
import { setAskAiHandlers } from "./askAiHandlersRef";

export type { AskAiHandlers, AskAiPayload } from "./askAiHandlersRef";

interface AskAiBridgeValue {
  handlers: AskAiHandlers | null;
  registerHandlers: (handlers: AskAiHandlers | null) => void;
}

const AskAiBridgeContext = createContext<AskAiBridgeValue | null>(null);

export function AskAiBridgeProvider({ children }: { children: ReactNode }) {
  const handlersRef = useRef<AskAiHandlers | null>(null);
  const [handlers, setHandlers] = useState<AskAiHandlers | null>(null);

  const registerHandlers = useCallback((next: AskAiHandlers | null) => {
    handlersRef.current = next;
    setHandlers(next);
    setAskAiHandlers(next);
  }, []);

  const value = useMemo(
    () => ({ handlers, registerHandlers }),
    [handlers, registerHandlers],
  );

  return <AskAiBridgeContext.Provider value={value}>{children}</AskAiBridgeContext.Provider>;
}

export function useAskAiBridge(): AskAiBridgeValue {
  const ctx = useContext(AskAiBridgeContext);
  if (!ctx) throw new Error("useAskAiBridge must be used within AskAiBridgeProvider");
  return ctx;
}

export function useRegisterAskAiHandlers(handlers: AskAiHandlers | null) {
  const { registerHandlers } = useAskAiBridge();
  useEffect(() => {
    registerHandlers(handlers);
    return () => registerHandlers(null);
  }, [handlers, registerHandlers]);
}
