import { useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "./usePanelApi";

export function useProjectFileIndex(enabled: boolean, refreshToken = 0) {
  const [files, setFiles] = useState<{ path: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const loadedRef = useRef(false);

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.list_project_file_paths) return;
    setLoading(true);
    try {
      const rows = await api.list_project_file_paths();
      setFiles(Array.isArray(rows) ? rows : []);
      loadedRef.current = true;
    } catch {
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void load();
  }, [enabled, load, refreshToken]);

  return { files, loading, reload: load };
}
