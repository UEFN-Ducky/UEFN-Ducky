import { useEffect, useState, type ReactNode } from "react";

import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";
import { loadModelsCatalog } from "../hooks/modelsCatalogCache";
import { DuckyParadeOverlay } from "./DuckyParade";

interface PanelApiGateProps {
  children: ReactNode;
  fallback?: ReactNode;
}

/** Wait for pywebview API before rendering children (required for focus windows). */
export function PanelApiGate({ children, fallback }: PanelApiGateProps) {
  const [ready, setReady] = useState(() => !!getApi());

  useEffect(() => onApiReady(() => {
    setReady(true);
    void loadModelsCatalog();
  }), []);

  if (!ready) {
    return fallback ?? <DuckyParadeOverlay label="Loading" />;
  }

  return <>{children}</>;
}


