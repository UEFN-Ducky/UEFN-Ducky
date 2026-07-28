import { useEffect, useRef, useState } from "react";

import { Modal, ModalActions } from "../components/Modal";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import type { AgentEvent } from "../types/panel";
import type { PendingTerminalCommand } from "./types";

function isPendingEvent(event: AgentEvent): event is AgentEvent & PendingTerminalCommand {
  return event.type === "terminal_command_pending" && !!event.request_id;
}

export function TerminalCommandApprovalProvider({ children }: { children: React.ReactNode }) {
  const [queue, setQueue] = useState<PendingTerminalCommand[]>([]);
  const [busy, setBusy] = useState(false);
  const queueRef = useRef(queue);
  queueRef.current = queue;

  useEffect(() => {
    return subscribeAgentEvents((event) => {
      if (!isPendingEvent(event)) return;
      const item: PendingTerminalCommand = {
        request_id: event.request_id!,
        session_id: event.session_id!,
        command: event.command!,
        shell: event.shell,
        cwd: event.cwd,
        conv_id: event.conv_id,
        source: event.source,
      };
      setQueue((prev) => [...prev, item]);
    });
  }, []);

  const current = queue[0] ?? null;

  const advance = () => setQueue((prev) => prev.slice(1));

  const handleAllow = async () => {
    if (!current || busy) return;
    setBusy(true);
    try {
      await getApi()?.terminal_approve_command(current.request_id);
    } finally {
      setBusy(false);
      advance();
    }
  };

  const handleDeny = async () => {
    if (!current || busy) return;
    setBusy(true);
    try {
      await getApi()?.terminal_reject_command(current.request_id);
    } finally {
      setBusy(false);
      advance();
    }
  };

  return (
    <>
      {children}
      <TerminalCommandApprovalModal
        open={!!current}
        pending={current}
        busy={busy}
        queueLength={queue.length}
        onAllow={() => void handleAllow()}
        onDeny={() => void handleDeny()}
      />
    </>
  );
}

interface TerminalCommandApprovalModalProps {
  open: boolean;
  pending: PendingTerminalCommand | null;
  busy: boolean;
  queueLength: number;
  onAllow: () => void;
  onDeny: () => void;
}

export function TerminalCommandApprovalModal({
  open,
  pending,
  busy,
  queueLength,
  onAllow,
  onDeny,
}: TerminalCommandApprovalModalProps) {
  const subtitle = pending?.source || pending?.conv_id || "Agent";
  return (
    <Modal
      open={open}
      onClose={onDeny}
      title="Allow terminal command?"
      width={520}
      footer={
        <ModalActions
          cancelLabel="Deny"
          confirmLabel="Allow"
          onCancel={onDeny}
          onConfirm={onAllow}
          confirmDisabled={busy}
        />
      }
    >
      <p className="terminal-approval-subtitle">
        Requested by <strong>{subtitle}</strong>
        {queueLength > 1 ? ` (${queueLength} in queue)` : null}
      </p>
      {pending?.shell || pending?.cwd ? (
        <p className="terminal-approval-meta">
          {[pending.shell, pending.cwd].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      <pre className="terminal-approval-command">{pending?.command ?? ""}</pre>
    </Modal>
  );
}
