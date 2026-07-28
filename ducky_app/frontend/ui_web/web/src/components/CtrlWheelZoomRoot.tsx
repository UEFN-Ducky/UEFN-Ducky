import { useEffect, useRef, type ReactNode } from "react";

import { formatZoomPercent, useCtrlWheelZoom } from "../hooks/useCtrlWheelZoom";
import { ScopedCss } from "../utils/scopedCss";

interface CtrlWheelZoomRootProps {
  className?: string;
  storageKey?: string;
  /** Fires whenever zoom changes (including restore from storage). */
  onZoomChange?: (zoom: number, prevZoom: number) => void;
  children: ReactNode;
}

export function CtrlWheelZoomRoot({
  className,
  storageKey,
  onZoomChange,
  children,
}: CtrlWheelZoomRootProps) {
  const { ref, zoom, scopeClass, indicatorVisible } = useCtrlWheelZoom({ storageKey });
  const prevZoomRef = useRef(zoom);
  const onZoomChangeRef = useRef(onZoomChange);
  onZoomChangeRef.current = onZoomChange;

  useEffect(() => {
    const prev = prevZoomRef.current;
    prevZoomRef.current = zoom;
    onZoomChangeRef.current?.(zoom, prev);
  }, [zoom]);

  return (
    <div ref={ref} className={["ctrl-wheel-zoom-host", scopeClass, className].filter(Boolean).join(" ")}>
      <ScopedCss selector={`.${scopeClass}`} rules={{ "--ctrl-wheel-zoom": String(zoom) }} />
      <div className="ctrl-wheel-zoom-content">
        {children}
      </div>
      <div
        className={`ctrl-wheel-zoom-indicator${indicatorVisible ? " ctrl-wheel-zoom-indicator--visible" : ""}`}
        aria-live="polite"
        aria-hidden={!indicatorVisible}
      >
        {formatZoomPercent(zoom)}
      </div>
    </div>
  );
}
