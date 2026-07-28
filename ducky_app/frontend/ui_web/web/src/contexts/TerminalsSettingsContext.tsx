import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";

interface TerminalsSettingsContextValue {
  enabled: boolean;
  loaded: boolean;
  setEnabled: (value: boolean) => Promise<void>;
}

const TerminalsSettingsContext = createContext<TerminalsSettingsContextValue | null>(null);

let terminalsEnabledRef = true;

export function getTerminalsEnabled(): boolean {
  return terminalsEnabledRef;
}

export function TerminalsSettingsProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    terminalsEnabledRef = enabled;
  }, [enabled]);

  useEffect(() => {
    let cancelled = false;
    const finish = () => {
      if (!cancelled) setLoaded(true);
    };
    const timeout = window.setTimeout(finish, 2500);
    // Terminals are always on (header control) — no General toggle.
    const stop = onApiReady((api) => {
      void api
        .get_settings()
        .then(async () => {
          setEnabledState(true);
          terminalsEnabledRef = true;
          if (api.save_agent_settings) {
            try {
              await api.save_agent_settings({ terminals_enabled: true });
            } catch {
              /* best-effort migrate old false */
            }
          }
        })
        .catch(() => {})
        .finally(finish);
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
      stop();
    };
  }, []);

  const setEnabled = useCallback(async (value: boolean) => {
    setEnabledState(value);
    terminalsEnabledRef = value;
    const api = getApi();
    if (api) {
      await api.save_agent_settings({ terminals_enabled: value });
    }
  }, []);

  const value = useMemo(() => ({ enabled, loaded, setEnabled }), [enabled, loaded, setEnabled]);

  return <TerminalsSettingsContext.Provider value={value}>{children}</TerminalsSettingsContext.Provider>;
}

export function useTerminalsSettings(): TerminalsSettingsContextValue {
  const ctx = useContext(TerminalsSettingsContext);
  if (!ctx) {
    return {
      enabled: true,
      loaded: false,
      setEnabled: async () => {},
    };
  }
  return ctx;
}
