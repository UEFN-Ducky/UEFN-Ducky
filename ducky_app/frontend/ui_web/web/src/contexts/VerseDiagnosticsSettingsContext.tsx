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
import { syncVerseDiagnosticsSettings } from "../verse-editor/diagnostics/verseDiagnosticsSettings";

interface VerseDiagnosticsSettingsContextValue {
  cacheEnabled: boolean;
  autoCheck: boolean;
  loaded: boolean;
  setCacheEnabled: (value: boolean) => Promise<void>;
  setAutoCheck: (value: boolean) => Promise<void>;
}

const VerseDiagnosticsSettingsContext = createContext<VerseDiagnosticsSettingsContextValue | null>(
  null,
);

export function VerseDiagnosticsSettingsProvider({ children }: { children: ReactNode }) {
  const [cacheEnabled, setCacheEnabledState] = useState(true);
  const [autoCheck, setAutoCheckState] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_settings().then((settings) => {
        syncVerseDiagnosticsSettings(settings);
        if (typeof settings.verse_diagnostics_cache_enabled === "boolean") {
          setCacheEnabledState(settings.verse_diagnostics_cache_enabled);
        }
        if (typeof settings.verse_diagnostics_auto_check === "boolean") {
          setAutoCheckState(settings.verse_diagnostics_auto_check);
        }
        setLoaded(true);
      });
    });
  }, []);

  const persist = useCallback(
    async (patch: {
      verse_diagnostics_cache_enabled?: boolean;
      verse_diagnostics_auto_check?: boolean;
    }) => {
      syncVerseDiagnosticsSettings(patch);
      const api = getApi();
      if (api) {
        await api.save_agent_settings(patch);
      }
    },
    [],
  );

  const setCacheEnabled = useCallback(
    async (value: boolean) => {
      setCacheEnabledState(value);
      await persist({ verse_diagnostics_cache_enabled: value });
    },
    [persist],
  );

  const setAutoCheck = useCallback(
    async (value: boolean) => {
      setAutoCheckState(value);
      await persist({ verse_diagnostics_auto_check: value });
    },
    [persist],
  );

  const value = useMemo(
    () => ({ cacheEnabled, autoCheck, loaded, setCacheEnabled, setAutoCheck }),
    [cacheEnabled, autoCheck, loaded, setCacheEnabled, setAutoCheck],
  );

  return (
    <VerseDiagnosticsSettingsContext.Provider value={value}>
      {children}
    </VerseDiagnosticsSettingsContext.Provider>
  );
}

export function useVerseDiagnosticsSettings(): VerseDiagnosticsSettingsContextValue {
  const ctx = useContext(VerseDiagnosticsSettingsContext);
  if (!ctx) {
    return {
      cacheEnabled: true,
      autoCheck: true,
      loaded: false,
      setCacheEnabled: async () => {},
      setAutoCheck: async () => {},
    };
  }
  return ctx;
}
