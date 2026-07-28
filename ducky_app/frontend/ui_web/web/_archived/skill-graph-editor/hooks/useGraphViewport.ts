import { useCallback, useState } from "react";
import { ZOOM_MAX, ZOOM_MIN } from "../constants/layout";

export interface ViewportState {
  pan: { x: number; y: number };
  zoom: number;
}

function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

export function useGraphViewport() {
  const [viewport, setViewport] = useState<ViewportState>({ pan: { x: 0, y: 0 }, zoom: 1 });

  const screenToWorld = useCallback(
    (sx: number, sy: number) => ({
      x: (sx - viewport.pan.x) / viewport.zoom,
      y: (sy - viewport.pan.y) / viewport.zoom,
    }),
    [viewport],
  );

  const setPan = useCallback((pan: { x: number; y: number }) => {
    setViewport((v) => ({ ...v, pan }));
  }, []);

  const resetViewport = useCallback(() => {
    setViewport({ pan: { x: 0, y: 0 }, zoom: 1 });
  }, []);

  const zoomBy = useCallback((delta: number, cursorX: number, cursorY: number) => {
    setViewport((v) => {
      const worldX = (cursorX - v.pan.x) / v.zoom;
      const worldY = (cursorY - v.pan.y) / v.zoom;
      const nextZoom = clampZoom(v.zoom + delta);
      return {
        zoom: nextZoom,
        pan: {
          x: cursorX - worldX * nextZoom,
          y: cursorY - worldY * nextZoom,
        },
      };
    });
  }, []);

  const zoomWheel = useCallback((deltaY: number, cursorX: number, cursorY: number) => {
    setViewport((v) => {
      const worldX = (cursorX - v.pan.x) / v.zoom;
      const worldY = (cursorY - v.pan.y) / v.zoom;
      const factor = 1 - deltaY * 0.001;
      const nextZoom = clampZoom(v.zoom * factor);
      return {
        zoom: nextZoom,
        pan: {
          x: cursorX - worldX * nextZoom,
          y: cursorY - worldY * nextZoom,
        },
      };
    });
  }, []);

  const setZoom = useCallback((zoom: number, centerX = 0, centerY = 0) => {
    setViewport((v) => {
      const nextZoom = clampZoom(zoom);
      const worldX = (centerX - v.pan.x) / v.zoom;
      const worldY = (centerY - v.pan.y) / v.zoom;
      return {
        zoom: nextZoom,
        pan: {
          x: centerX - worldX * nextZoom,
          y: centerY - worldY * nextZoom,
        },
      };
    });
  }, []);

  return {
    viewport,
    setPan,
    resetViewport,
    zoomBy,
    zoomWheel,
    setZoom,
    screenToWorld,
  };
}
