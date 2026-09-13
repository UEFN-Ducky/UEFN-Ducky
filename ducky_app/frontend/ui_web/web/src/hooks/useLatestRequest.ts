import { useCallback, useEffect, useRef } from "react";

/** Only the newest request for the mounted resource may update its view. */
export function useLatestRequest(resource: string) {
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, [resource]);
  return useCallback(() => {
    const request = ++generation.current;
    return () => request === generation.current;
  }, []);
}
