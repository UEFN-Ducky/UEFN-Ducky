import { useCallback, useEffect, useState } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type { SandboxStatus } from "../types/panel";

// Refresh while the section is open only; a closed section costs nothing.
const POLL_MS = 5000;

/** The chat's sandbox: size, running programs (with Stop and their ports), the pictures an
 * agent took as evidence, Open folder and Delete. Hidden until the chat has a sandbox. */
export function SandboxSection({ convId }: { convId: string }) {
  const { confirm } = useConfirmModal();
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.sandbox_status || !convId) return;
    try {
      setStatus(await api.sandbox_status(convId));
    } catch {
      setStatus(null);
    }
  }, [convId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    return setVisibleInterval(() => void refresh(), POLL_MS);
  }, [open, refresh]);

  if (!status?.ok || !status.exists) return null;

  const programs = status.programs ?? [];
  const stop = async (pid: number) => {
    setBusy(`stop-${pid}`);
    await getApi()?.sandbox_stop_program?.(convId, pid);
    setBusy("");
    void refresh();
  };
  const remove = async () => {
    const ok = await confirm({
      title: "Delete this chat's sandbox?",
      message: "Stops its programs, removes its worktrees and deletes everything in it. Your projects don't change.",
      confirmLabel: "Delete sandbox",
      danger: true,
    });
    if (!ok) return;
    setBusy("delete");
    await getApi()?.sandbox_delete?.(convId);
    setBusy("");
    void refresh();
  };
  const openPort = (port: number) =>
    void getApi()?.plugin_call?.("browser", "open_page", { url: `localhost:${port}` });

  return (
    <section className="context-usage-panel-accordion-section sandbox-section">
      <button type="button" onClick={() => setOpen((v) => !v)} className="context-usage-panel-accordion-toggle">
        <span className={`context-usage-panel-accordion-chevron${open ? " context-usage-panel-accordion-chevron--expanded" : ""}`}>
          <Icons.ChevronDown />
        </span>
        <span className="context-usage-panel-accordion-title">Sandbox</span>
        <span className="context-usage-panel-accordion-summary">
          {programs.length ? `${programs.length} running · ` : ""}
          {status.size_mb ?? 0} MB
        </span>
      </button>
      {open ? (
        <div className="context-usage-panel-accordion-body sandbox-section-body">
          {status.size_warning ? (
            <div className="sandbox-section-warning">Over 5 GB: delete it when you are done.</div>
          ) : null}
          {programs.length ? (
            <ul className="sandbox-section-list">
              {programs.map((p) => (
                <li key={p.pid} className="sandbox-section-row">
                  <span className="sandbox-section-name" title={(p.command ?? []).join(" ")}>{p.name}</span>
                  <span className="sandbox-section-meta">
                    {p.memory_mb != null ? `${p.memory_mb} MB` : ""}
                    {p.cpu_percent ? ` · ${p.cpu_percent}% CPU` : ""}
                  </span>
                  {[...new Set([...Object.values(p.ports ?? {}), ...(p.listening ?? [])])].slice(0, 3).map((port) => (
                    <button key={port} type="button" className="sandbox-section-chip" onClick={() => openPort(port)}
                      title={`Open localhost:${port} in the Browser tab`}>
                      :{port}
                    </button>
                  ))}
                  <button type="button" className="sandbox-section-chip" disabled={busy === `stop-${p.pid}`}
                    onClick={() => void stop(p.pid)}>
                    Stop
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="sandbox-section-empty">Nothing running.</div>
          )}
          {status.pictures?.length ? (
            <div className="sandbox-section-pictures">
              {status.pictures.map((pic) => (
                <img key={pic.path} src={pic.media_url} alt="Sandbox evidence" loading="lazy" />
              ))}
            </div>
          ) : null}
          <div className="sandbox-section-actions">
            <button type="button" className="settings-btn" onClick={() => void getApi()?.sandbox_open_folder?.(convId)}>
              <Icons.Folder /> Open folder
            </button>
            {programs.length > 1 ? (
              <button type="button" className="settings-btn" onClick={() => void stop(0)}>Stop all</button>
            ) : null}
            <button type="button" className="settings-btn is-danger" disabled={busy === "delete"} onClick={() => void remove()}>
              <Icons.Trash /> Delete
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
