import { useCallback, useEffect, useRef, useState } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { ChoiceDropdown } from "./ChoiceDropdown";

export type WindowViewRow = { id: string; title: string; kind?: string };

export function RemoteWindowSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const [rows, setRows] = useState<WindowViewRow[]>([]);

  const load = useCallback(async () => {
    if (!isRemote()) return;
    const api = getApi();
    if (!api?.list_window_views) return;
    try {
      const next = await api.list_window_views();
      if (Array.isArray(next)) setRows(next);
    } catch {
      setRows([]);
    }
  }, []);

  useEffect(() => {
    if (!isRemote()) return;
    void load();
    const id = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(id);
  }, [load]);

  if (!isRemote()) return null;

  return (
    <div className="remote-window-select no-drag" onPointerDown={() => void load()}>
      <ChoiceDropdown
        size="compact"
        aria-label="View"
        value={value}
        minWidth={240}
        placeholder="View"
        fixedLabel="View"
        onChange={onChange}
        options={[
          { value: "", label: "Ducky", group: "This app" },
          ...rows.map((row) => ({
            value: row.id,
            label: row.title,
            group:
              row.kind === "uefn" ? "UEFN" : row.kind === "blender" ? "Blender" : "Windows",
          })),
        ]}
      />
    </div>
  );
}

function wsUrl(hwnd: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/__window_stream?id=${encodeURIComponent(hwnd)}`;
}

function normPoint(ev: { currentTarget: HTMLCanvasElement; clientX: number; clientY: number }) {
  const r = ev.currentTarget.getBoundingClientRect();
  const w = r.width || 1;
  const h = r.height || 1;
  return {
    x: Math.min(1, Math.max(0, (ev.clientX - r.left) / w)),
    y: Math.min(1, Math.max(0, (ev.clientY - r.top) / h)),
  };
}

function sendOverlaySize(ws: WebSocket, el: HTMLElement | null) {
  if (ws.readyState !== WebSocket.OPEN || !el) return;
  const r = el.getBoundingClientRect();
  if (r.width < 80 || r.height < 80) return;
  const dpr = window.devicePixelRatio || 1;
  ws.send(
    JSON.stringify({
      type: "size",
      w: Math.round(r.width * dpr),
      h: Math.round(r.height * dpr),
    }),
  );
}

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const lastMove = useRef(0);
  const lastSize = useRef(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!hwnd) return;
    setFailed(false);
    const ws = new WebSocket(wsUrl(hwnd));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    let gotFrame = false;
    let frameGen = 0;
    const paint = (src: CanvasImageSource, w: number, h: number, gen: number) => {
      if (gen !== frameGen) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (canvas.width !== w) canvas.width = w;
      if (canvas.height !== h) canvas.height = h;
      canvas.getContext("2d")?.drawImage(src, 0, 0);
      gotFrame = true;
      setFailed(false);
    };
    ws.onopen = () => sendOverlaySize(ws, overlayRef.current);
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return;
      const gen = ++frameGen;
      const blob = new Blob([ev.data], { type: "image/jpeg" });
      if (typeof createImageBitmap === "function") {
        void createImageBitmap(blob).then(
          (bmp) => {
            try {
              paint(bmp, bmp.width, bmp.height, gen);
            } finally {
              bmp.close();
            }
          },
          () => {},
        );
        return;
      }
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        paint(img, img.width, img.height, gen);
        URL.revokeObjectURL(url);
      };
      img.onerror = () => URL.revokeObjectURL(url);
      img.src = url;
    };
    ws.onclose = () => {
      if (wsRef.current === ws && !gotFrame) setFailed(true);
    };
    return () => {
      wsRef.current = null;
      ws.close();
    };
  }, [hwnd]);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el || !hwnd || failed) return;
    const ro = new ResizeObserver(() => {
      const now = performance.now();
      if (now - lastSize.current < 200) return;
      lastSize.current = now;
      const sock = wsRef.current;
      if (sock) sendOverlaySize(sock, el);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [hwnd, failed]);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el || !hwnd || failed) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const r = el.getBoundingClientRect();
      const w = r.width || 1;
      const h = r.height || 1;
      ws.send(
        JSON.stringify({
          type: "wheel",
          x: Math.min(1, Math.max(0, (ev.clientX - r.left) / w)),
          y: Math.min(1, Math.max(0, (ev.clientY - r.top) / h)),
          delta: ev.deltaY > 0 ? 1 : -1,
        }),
      );
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [hwnd, failed]);

  const send = (payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(payload));
  };

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay" ref={overlayRef}>
      {failed ? (
        <p className="remote-window-overlay-msg">Window unavailable (minimized or closed).</p>
      ) : (
        <canvas
          ref={canvasRef}
          tabIndex={0}
          className="remote-window-canvas"
          onPointerDown={(ev) => {
            ev.preventDefault();
            ev.currentTarget.focus();
            ev.currentTarget.setPointerCapture(ev.pointerId);
            const { x, y } = normPoint(ev);
            send({ type: "down", x, y, button: ev.button });
          }}
          onPointerUp={(ev) => {
            const { x, y } = normPoint(ev);
            send({ type: "up", x, y, button: ev.button });
          }}
          onPointerMove={(ev) => {
            const now = performance.now();
            if (ev.buttons === 0 && now - lastMove.current < 25) return;
            lastMove.current = now;
            const { x, y } = normPoint(ev);
            send({ type: "move", x, y, button: ev.button });
          }}
          onKeyDown={(ev) => {
            ev.preventDefault();
            send({ type: "keydown", key: ev.key });
          }}
          onKeyUp={(ev) => {
            ev.preventDefault();
            send({ type: "keyup", key: ev.key });
          }}
        />
      )}
    </div>
  );
}
