import { useEffect, useState } from "react";
import { getApi, isRemote } from "../hooks/usePanelApi";

export type WindowViewRow = { id: string; title: string; kind?: string };

export function RemoteWindowSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const [rows, setRows] = useState<WindowViewRow[]>([]);

  useEffect(() => {
    if (!isRemote()) return;
    let cancelled = false;
    const load = async () => {
      const api = getApi();
      if (!api?.list_window_views) return;
      try {
        const next = await api.list_window_views();
        if (!cancelled && Array.isArray(next)) setRows(next);
      } catch {
        if (!cancelled) setRows([]);
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (!isRemote()) return null;

  return (
    <label className="remote-window-select no-drag">
      <span className="remote-window-select-label">View</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        title="Look at another window on this PC"
      >
        <option value="">Ducky</option>
        {rows.map((row) => (
          <option key={row.id} value={row.id}>
            {row.title}
          </option>
        ))}
      </select>
    </label>
  );
}

export function RemoteWindowOverlay({ hwnd }: { hwnd: string }) {
  const [tick, setTick] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    const id = window.setInterval(() => setTick((n) => n + 1), 450);
    return () => window.clearInterval(id);
  }, [hwnd]);

  if (!hwnd) return null;

  return (
    <div className="remote-window-overlay">
      {failed ? (
        <p className="remote-window-overlay-msg">Window unavailable (minimized or closed).</p>
      ) : (
        <img
          alt=""
          src={`/__window_view?id=${encodeURIComponent(hwnd)}&t=${tick}`}
          onError={() => setFailed(true)}
          onLoad={() => setFailed(false)}
        />
      )}
    </div>
  );
}
