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

import type { ProblemsDuckyHandlers } from "./problemsDuckyHandlersRef";
import { setProblemsDuckyHandlers } from "./problemsDuckyHandlersRef";

export type { ProblemsDuckyHandlers } from "./problemsDuckyHandlersRef";

interface ProblemsDuckyBridgeValue {
  handlers: ProblemsDuckyHandlers | null;
  registerHandlers: (handlers: ProblemsDuckyHandlers | null) => void;
}

const ProblemsDuckyBridgeContext = createContext<ProblemsDuckyBridgeValue | null>(null);

export function ProblemsDuckyBridgeProvider({ children }: { children: ReactNode }) {
  const handlersRef = useRef<ProblemsDuckyHandlers | null>(null);
  const [handlers, setHandlers] = useState<ProblemsDuckyHandlers | null>(null);

  const registerHandlers = useCallback((next: ProblemsDuckyHandlers | null) => {
    handlersRef.current = next;
    setHandlers(next);
    setProblemsDuckyHandlers(next);
  }, []);

  const value = useMemo(
    () => ({ handlers, registerHandlers }),
    [handlers, registerHandlers],
  );

  return (
    <ProblemsDuckyBridgeContext.Provider value={value}>{children}</ProblemsDuckyBridgeContext.Provider>
  );
}

export function useProblemsDuckyBridge(): ProblemsDuckyBridgeValue {
  const ctx = useContext(ProblemsDuckyBridgeContext);
  if (!ctx) throw new Error("useProblemsDuckyBridge must be used within ProblemsDuckyBridgeProvider");
  return ctx;
}

export function useRegisterProblemsDuckyHandlers(handlers: ProblemsDuckyHandlers | null) {
  const { registerHandlers } = useProblemsDuckyBridge();
  useEffect(() => {
    registerHandlers(handlers);
    return () => registerHandlers(null);
  }, [handlers, registerHandlers]);
}
