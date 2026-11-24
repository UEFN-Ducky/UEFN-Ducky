/**
 * A phone, browser or other PC asks to control this PC: Allow or Deny, here only.
 * The backend refuses remote_pair_decide from anything but this PC's own window.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Modal } from "./Modal";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi, isRemote } from "../hooks/usePanelApi";

export type PairRequest = { id: string; label: string; code: string };

export function RemotePairModal() {
  const [queue, setQueue] = useState<PairRequest[]>([]);
  const [error, setError] = useState("");
  const denyRef = useRef<HTMLButtonElement>(null);
  const current = queue[0];

  const add = useCallback((rows: PairRequest[]) => {
    setQueue((q) => [...q, ...rows.filter((r) => r?.id && !q.some((x) => x.id === r.id))]);
  }, []);

  useEffect(() => {
    if (isRemote()) return;
    const load = () => {
      void getApi()?.remote_devices_list?.().then((r) => add(r?.pending ?? [])).catch(() => {});
    };
    load();
    window.addEventListener("focus", load);
    const off = subscribeAgentEvents((event) => {
      if (String(event.type) !== "remote_pair_request") return;
      const e = event as unknown as PairRequest;
      add([{ id: String(e.id || ""), label: String(e.label || ""), code: String(e.code || "") }]);
    });
    return () => {
      window.removeEventListener("focus", load);
      off();
    };
  }, [add]);

  // Deny holds focus, so a stray Enter or Space never approves.
  useEffect(() => {
    if (current) denyRef.current?.focus();
  }, [current]);

  const decide = async (allow: boolean) => {
    if (!current) return;
    const r = await getApi()?.remote_pair_decide?.(current.id, allow).catch(() => null);
    if (allow && !r?.ok) {
      setError(r?.error || "Couldn't approve that device.");
      return;
    }
    setError("");
    setQueue((q) => q.slice(1));
  };

  if (isRemote()) return null;
  return (
    <Modal
      open={!!current}
      onClose={() => void decide(false)}
      title="Allow a new device?"
      width={440}
      closeOnBackdrop={false}
      footer={
        <div className="modal-actions-row">
          <button type="button" ref={denyRef} className="settings-btn" onClick={() => void decide(false)}>
            Deny
          </button>
          <button
            type="button"
            className="settings-btn modal-confirm-btn"
            // Enter never approves; a click or Space does.
            onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
            onClick={() => void decide(true)}
          >
            Allow
          </button>
        </div>
      }
    >
      {current ? (
        <div className="remote-pair-body">
          <p>
            <strong>{current.label || "A device"}</strong> wants to control this PC's UEFN Ducky.
          </p>
          <p className="remote-pair-code" aria-label={`Code ${current.code.split("").join(" ")}`}>
            {current.code}
          </p>
          <p>Allow it only if the device shows this same code. If you didn't start this, choose Deny.</p>
          {error ? <p className="remote-pair-error" role="alert">{error}</p> : null}
        </div>
      ) : null}
    </Modal>
  );
}
