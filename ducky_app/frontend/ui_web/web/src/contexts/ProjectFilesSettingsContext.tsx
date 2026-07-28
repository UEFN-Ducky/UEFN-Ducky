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

interface ProjectFilesSettingsContextValue {
  showHiddenFiles: boolean;
  loaded: boolean;
  treeRefreshToken: number;
  setShowHiddenFiles: (value: boolean) => Promise<void>;
}

const ProjectFilesSettingsContext = createContext<ProjectFilesSettingsContextValue | null>(null);

export function ProjectFilesSettingsProvider({ children }: { children: ReactNode }) {
  const [showHiddenFiles, setShowHiddenFilesState] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [treeRefreshToken, setTreeRefreshToken] = useState(0);

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_settings().then((settings) => {
        if (typeof settings.show_hidden_project_files === "boolean") {
          setShowHiddenFilesState(settings.show_hidden_project_files);
        }
        setLoaded(true);
      });
    });
  }, []);

  const setShowHiddenFiles = useCallback(async (value: boolean) => {
    setShowHiddenFilesState(value);
    const api = getApi();
    if (api) {
      await api.save_agent_settings({ show_hidden_project_files: value });
    }
    setTreeRefreshToken((n) => n + 1);
  }, []);

  const value = useMemo(
    () => ({ showHiddenFiles, loaded, treeRefreshToken, setShowHiddenFiles }),
    [showHiddenFiles, loaded, treeRefreshToken, setShowHiddenFiles],
  );

  return (
    <ProjectFilesSettingsContext.Provider value={value}>{children}</ProjectFilesSettingsContext.Provider>
  );
}

export function useProjectFilesSettings(): ProjectFilesSettingsContextValue {
  const ctx = useContext(ProjectFilesSettingsContext);
  if (!ctx) {
    return {
      showHiddenFiles: false,
      loaded: false,
      treeRefreshToken: 0,
      setShowHiddenFiles: async () => {},
    };
  }
  return ctx;
}
