import { useCallback, useEffect, useRef, useState } from "react";

import { useScopedClass } from "../utils/scopedCss";



const ZOOM_MIN = 0.5;

const ZOOM_MAX = 2.0;

const ZOOM_DEFAULT = 1;

const INDICATOR_HIDE_MS = 1200;



function clampZoom(value: number): number {

  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));

}



function readStoredZoom(storageKey: string | undefined): number {

  if (!storageKey) return ZOOM_DEFAULT;

  try {

    const raw = localStorage.getItem(storageKey);

    if (raw === null) return ZOOM_DEFAULT;

    const parsed = Number(raw);

    if (!Number.isFinite(parsed)) return ZOOM_DEFAULT;

    return clampZoom(parsed);

  } catch {

    return ZOOM_DEFAULT;

  }

}



function persistZoom(storageKey: string | undefined, zoom: number): void {

  if (!storageKey) return;

  try {

    localStorage.setItem(storageKey, String(zoom));

  } catch {

    // ignore storage errors

  }

}



export function formatZoomPercent(zoom: number): string {

  return `${Math.round(zoom * 100)}%`;

}



export function sidebarPanelZoomKey(panelId: string): string {

  return `uefn-panel-sidebar-zoom:${panelId}`;

}



export interface UseCtrlWheelZoomOptions {

  storageKey?: string;

  /** Capture phase — needed when children (e.g. Monaco) consume wheel events. */

  capture?: boolean;

}



export function useCtrlWheelZoom({ storageKey, capture = false }: UseCtrlWheelZoomOptions = {}) {

  const ref = useRef<HTMLDivElement>(null);

  const scopeClass = useScopedClass("ctrl-wheel-zoom");

  const [zoom, setZoom] = useState(() => readStoredZoom(storageKey));

  const [indicatorVisible, setIndicatorVisible] = useState(false);

  const zoomRef = useRef(zoom);

  const hideTimerRef = useRef<number | null>(null);

  zoomRef.current = zoom;



  const bumpIndicator = useCallback(() => {

    setIndicatorVisible(true);

    if (hideTimerRef.current !== null) {

      window.clearTimeout(hideTimerRef.current);

    }

    hideTimerRef.current = window.setTimeout(() => {

      setIndicatorVisible(false);

      hideTimerRef.current = null;

    }, INDICATOR_HIDE_MS);

  }, []);



  const updateZoom = useCallback(

    (next: number) => {

      const clamped = clampZoom(next);

      setZoom(clamped);

      persistZoom(storageKey, clamped);

    },

    [storageKey],

  );



  useEffect(() => {

    setZoom(readStoredZoom(storageKey));

  }, [storageKey]);



  useEffect(() => {

    const el = ref.current;

    if (!el) return;



    const onWheel = (e: WheelEvent) => {

      if (!e.ctrlKey) return;

      e.preventDefault();

      updateZoom(zoomRef.current + -e.deltaY * 0.001);

      bumpIndicator();

    };



    el.addEventListener("wheel", onWheel, { passive: false, capture });

    return () => el.removeEventListener("wheel", onWheel, capture);

  }, [updateZoom, bumpIndicator, capture]);



  useEffect(() => {

    return () => {

      if (hideTimerRef.current !== null) {

        window.clearTimeout(hideTimerRef.current);

      }

    };

  }, []);



  return { ref, zoom, scopeClass, indicatorVisible };

}

