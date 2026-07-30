import type { AgentEvent, ChatMessage, MessageAttachmentDto } from "../../types/panel";

/**
 * The one chat-run flow, as a pure state machine.
 *
 *          send()/resend()
 *   idle ─────────────────► sending ──sendAccepted(runId)──► running ──┐
 *    ▲                         │                               │       │ delta / thinking /
 *    │                stopOptimistic                           │◄──────┘ tool / toolResult
 *    │                         ▼                               │        (append, stay running)
 *    │                       idle                              │
 *    │                                          stop /         ▼
 *    │  agentEvent(agent_stopped | error) / reconcile(miss×2)  │
 *    └─────────────────────────────────────────────────────────┘
 *
 * Everything that used to be scattered across handleAgentEvent, appendUserMessage,
 * rewindAndAppendUser, the reconcile poll, and the stop handler lives here as
 * explicit transitions. The reducer is pure and owns ALL run + message-tail
 * state; the hook that wraps it only does I/O (fetch, subscribe, poll, cache)
 * and translates results back into actions.
 *
 * Message-tail semantics preserved from the old hook:
 *   • Optimistic rows minted here get transient string ids ("opt-N") so they
 *     never collide with the backend's numeric ids; a later `loaded` while idle
 *     replaces the whole tail with the canonical copy.
 *   • `reloadToken` is a command channel: whenever a transition needs the backend
 *     copy pulled, it bumps the token and the wrapping hook runs `load()`.
 */

export type RunStatus = "idle" | "sending" | "running";

export interface RunState {
  /** idle = no run; sending = dispatched, awaiting run_id; running = accepted / streaming. */
  status: RunStatus;
  /** Active backend run id, once send_message resolves. Null while sending/idle. */
  runId: string | null;
  /** Latched by a user stop: ignore all further events until the next send. */
  stopped: boolean;
  /** Mirror of the global useRunningAgents membership for this chat (isAgentRunning prop). */
  externalRunning: boolean;
  messages: ChatMessage[];
  /** Live answer text streaming in for the current turn. */
  stream: string;
  /** Live reasoning text streaming in for the current turn. */
  thinking: string;
  /** Last backend status line (e.g. "Starting Cursor…") for the activity footer. */
  statusText: string;
  atBottom: boolean;
  hasNewBelow: boolean;
  /** Bumped whenever a transition wants the wrapping hook to reload from backend. */
  reloadToken: number;
  /** Monotonic source of transient optimistic-row ids. */
  idSeq: number;
}

export type RunAction =
  | { type: "send"; text: string; attachments?: MessageAttachmentDto[] }
  | { type: "resend"; text: string; attachments?: MessageAttachmentDto[] }
  | { type: "sendAccepted"; runId: string }
  | { type: "stopOptimistic" }
  | { type: "stop" }
  | { type: "invalidate" }
  | { type: "clearStream" }
  | { type: "agentEvent"; event: AgentEvent }
  | { type: "loaded"; rows: ChatMessage[] }
  | { type: "externalRunning"; running: boolean }
  | { type: "reconcile"; running: boolean }
  | { type: "atBottom"; atBottom: boolean };

export const initialRunState: RunState = {
  status: "idle",
  runId: null,
  stopped: false,
  externalRunning: false,
  messages: [],
  stream: "",
  thinking: "",
  statusText: "",
  atBottom: true,
  hasNewBelow: false,
  reloadToken: 0,
  idSeq: 0,
};

/** Content fingerprint used to recognise an optimistic row's canonical twin.
 *  Role + text + tool name + attachment count is enough to match the just-sent
 *  user message against the copy the backend persisted under a numeric id. */
function contentSig(m: ChatMessage): string {
  const attach = m.attachments?.length ?? 0;
  return `${m.role}\u0000${m.text ?? ""}\u0000${m.tool?.name ?? ""}\u0000${attach}`;
}

/** While a run is live, keep the optimistic tail on screen and only fold in
 *  committed rows we don't already have.
 *
 *  Optimistic rows carry transient "opt-N" ids that never match the backend's
 *  numeric ids, so a mid-run reload (e.g. a `context_changed` fired right after
 *  send, once the backend has persisted the user message) would fold in a SECOND
 *  copy of a message already on screen — two identical user bubbles. Guard the
 *  id check with a content fingerprint of the optimistic rows so the canonical
 *  twin is recognised as already-present and skipped. The next idle reload
 *  replaces the whole tail with the canonical rows, retiring the opt ids. */
export function mergeCommitted(existing: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const seen = new Set(existing.map((m) => String(m.id)));
  const optimisticSigs = new Set(
    existing
      .filter((m) => String(m.id).startsWith("opt-") && (m.text?.trim() || m.attachments?.length))
      .map(contentSig),
  );
  const missing = incoming.filter(
    (m) => !seen.has(String(m.id)) && !optimisticSigs.has(contentSig(m)),
  );
  if (missing.length === 0) return existing;
  return [...missing, ...existing];
}

/** A late event belongs to the current run when: we haven't been stopped, and it
 *  either carries no run id or matches the one we're tracking (a null runId means
 *  we sent but haven't learned the id yet, so accept anything). */
export function eventMatchesRun(event: AgentEvent, runId: string | null, stopped: boolean): boolean {
  if (stopped) return false;
  if (!event.run_id) return true;
  if (!runId) return true;
  return event.run_id === runId;
}

function optId(seq: number): string {
  return `opt-${seq}`;
}

function markNewBelow(state: RunState): boolean {
  return state.atBottom ? state.hasNewBelow : true;
}

/** Fold live stream/thinking into a committed bubble so Stop keeps what was on screen. */
function flushPartialToMessages(
  state: RunState,
  opts?: { error?: string },
): { messages: ChatMessage[]; idSeq: number } {
  if (!state.stream.trim() && !state.thinking.trim()) {
    return { messages: state.messages, idSeq: state.idSeq };
  }
  const seq = state.idSeq;
  const row: ChatMessage = {
    id: optId(seq),
    role: "assistant",
    text: state.stream,
    thinking: state.thinking || undefined,
    incomplete: true,
    error: opts?.error,
  };
  return { messages: [...state.messages, row], idSeq: seq + 1 };
}

/** A stop that lands while a tool call is still executing leaves its intent row
 *  on screen with no result, so ToolExecutionCard spins on "running…" forever.
 *  Close that dangling tool (the trailing "tool" row) with a synthetic
 *  "cancelled" result so the card flips to CANCELED. */
function closeDanglingTool(
  messages: ChatMessage[],
  idSeq: number,
): { messages: ChatMessage[]; idSeq: number } {
  const last = messages[messages.length - 1];
  if (!last || last.role !== "tool") {
    return { messages, idSeq };
  }
  const row: ChatMessage = {
    id: optId(idSeq),
    role: "error",
    text: "",
    tool: {
      name: last.tool?.name ?? "tool",
      arguments: last.tool?.arguments ?? {},
      status: "cancelled",
    },
  };
  return { messages: [...messages, row], idSeq: idSeq + 1 };
}

/** Stop-time tail fold, shared by the user stop action and the backend cancel
 *  events: close any in-flight tool as cancelled, then commit whatever partial
 *  stream/thinking was on screen as a "Stopped" bubble. */
function foldStoppedTail(state: RunState): { messages: ChatMessage[]; idSeq: number } {
  const closed = closeDanglingTool(state.messages, state.idSeq);
  return flushPartialToMessages(
    { ...state, messages: closed.messages, idSeq: closed.idSeq },
    { error: "Stopped" },
  );
}

/** True when a run is in flight from the point of view of a backend reload: don't
 *  clobber the optimistic tail while the local run OR the global running-set says
 *  something is live. A user stop wins outright — once `stopped` is latched we want
 *  the next reload to replace the tail with the canonical copy. */
export function isInFlight(state: RunState): boolean {
  if (state.stopped) return false;
  return state.status !== "idle" || state.externalRunning;
}

/** Derived: should the Stop button / activity panel show.
 *
 *  The user's stop latch beats `externalRunning`. Otherwise a run whose backend
 *  thread is wedged in a blocking tool call stays in the global running-set, so
 *  `externalRunning` holds the Stop button visible and every further press is a
 *  no-op ("Stop does nothing / it's stuck"). Latching off `stopped` makes the very
 *  first press go idle; the backend still gets its cancel flag and unwinds at the
 *  next checkpoint, and the next send clears `stopped` (see startTurn). */
export function isRunActive(state: RunState): boolean {
  if (state.stopped) return false;
  return state.status !== "idle" || state.externalRunning;
}

function reset(state: RunState, patch: Partial<RunState>): RunState {
  return { ...state, ...patch };
}

function startTurn(state: RunState, messages: ChatMessage[]): RunState {
  return reset(state, {
    messages,
    status: "sending",
    runId: null,
    stopped: false,
    stream: "",
    thinking: "",
    statusText: "",
  });
}

function applyAgentEvent(state: RunState, event: AgentEvent): RunState {
  if (!eventMatchesRun(event, state.runId, state.stopped)) return state;

  switch (event.type) {
    case "context_changed":
      return reset(state, { reloadToken: state.reloadToken + 1 });

    case "text_delta":
      return reset(state, {
        status: "running",
        stream: state.stream + (event.text ?? ""),
        statusText: "",
        hasNewBelow: markNewBelow(state),
      });

    case "thinking":
      return reset(state, {
        status: "running",
        thinking: state.thinking + (event.text ?? ""),
        statusText: "",
        hasNewBelow: markNewBelow(state),
      });

    case "status": {
      const text = (event.text ?? "").trim();
      if (!text) return state;
      return reset(state, {
        status: state.status === "idle" ? "running" : state.status,
        statusText: text,
      });
    }

    case "tool": {
      // Flush reasoning/answer streamed before this tool into committed bubbles so
      // they stay interleaved *above* the tool (Cursor-style) while still live.
      const flushed: ChatMessage[] = [];
      let seq = state.idSeq;
      if (state.thinking.trim()) {
        flushed.push({ id: optId(seq++), role: "assistant", text: "", thinking: state.thinking });
      }
      if (state.stream.trim()) {
        flushed.push({ id: optId(seq++), role: "assistant", text: state.stream });
      }
      const toolRow: ChatMessage = { id: optId(seq++), role: "tool", text: event.text ?? "", tool: event.tool };
      return reset(state, {
        status: "running",
        messages: [...state.messages, ...flushed, toolRow],
        stream: "",
        thinking: "",
        statusText: "",
        idSeq: seq,
        hasNewBelow: markNewBelow(state),
      });
    }

    case "tool_done": {
      const seq = state.idSeq;
      const row: ChatMessage = {
        id: optId(seq),
        role: event.success ? "success" : "error",
        text: event.text ?? "",
        tool: event.tool,
      };
      return reset(state, {
        messages: [...state.messages, row],
        idSeq: seq + 1,
        hasNewBelow: markNewBelow(state),
      });
    }

    case "delegation_warning": {
      const seq = state.idSeq;
      const row: ChatMessage = {
        id: optId(seq),
        role: "error",
        text: `⚠ ${event.text ?? "Delegation was described but not executed."}`,
      };
      return reset(state, {
        messages: [...state.messages, row],
        idSeq: seq + 1,
        hasNewBelow: markNewBelow(state),
      });
    }

    case "assistant_done": {
      // Group orchestrator may push the full reply on the event without prior
      // text_delta (or after a missed delta) — prefer stream, fall back to event.text.
      const answer = state.stream.trim() || (event.text ?? "").trim();
      if (!answer && !state.thinking.trim()) {
        return reset(state, { stream: "", thinking: "", statusText: "" });
      }
      const seq = state.idSeq;
      const row: ChatMessage = {
        id: optId(seq),
        role: "assistant",
        text: answer || state.stream,
        thinking: state.thinking || undefined,
        ...(event.author ? { author: event.author } : {}),
      };
      return reset(state, {
        messages: [...state.messages, row],
        stream: "",
        thinking: "",
        statusText: "",
        idSeq: seq + 1,
        hasNewBelow: markNewBelow(state),
      });
    }

    case "agent_stopped": {
      const reason = event.reason ?? "error";
      const flushed = reason === "cancelled" ? foldStoppedTail(state) : null;
      const base = reset(state, {
        status: "idle",
        runId: null,
        stream: "",
        thinking: "",
        statusText: "",
        ...(flushed ? { messages: flushed.messages, idSeq: flushed.idSeq } : {}),
      });
      if (reason === "cancelled") {
        return base;
      }
      if (reason === "done") {
        // reconcileTail: pull canonical copy if the user is at the bottom, else
        // just flag that there's something new below without yanking their scroll.
        return state.atBottom
          ? reset(base, { reloadToken: base.reloadToken + 1 })
          : reset(base, { hasNewBelow: true });
      }
      // Error stop: the preceding "error" event already committed the partial;
      // leave it and let a later clean reload reconcile.
      return base;
    }

    case "error": {
      if (event.text === "Cancelled") {
        const flushed = foldStoppedTail(state);
        return reset(state, {
          status: "idle",
          runId: null,
          stopped: true,
          messages: flushed.messages,
          stream: "",
          thinking: "",
          statusText: "",
          idSeq: flushed.idSeq,
        });
      }
      // Always use an interrupted assistant bubble — role "error" alone is
      // reserved for tool results and was invisible as a standalone chat row.
      const errText = event.text ?? "Error";
      const seq = state.idSeq;
      const row: ChatMessage = {
        id: optId(seq),
        role: "assistant",
        text: state.stream,
        thinking: state.thinking || undefined,
        incomplete: true,
        error: errText,
      };
      return reset(state, {
        status: "idle",
        runId: null,
        messages: [...state.messages, row],
        stream: "",
        thinking: "",
        statusText: "",
        idSeq: seq + 1,
        hasNewBelow: markNewBelow(state),
      });
    }

    default:
      // Non-stream events (usage/linked_agent/editor/etc.) don't touch the
      // run/message state here — the hook handles those separately.
      return state;
  }
}

export function chatRunReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "send": {
      const userMsg: ChatMessage = {
        id: optId(state.idSeq),
        role: "user",
        text: action.text,
        attachments: action.attachments?.length ? action.attachments : undefined,
      };
      return startTurn(reset(state, { idSeq: state.idSeq + 1 }), [...state.messages, userMsg]);
    }

    case "resend": {
      // Drop the last user turn (last "user" row + everything after) and append
      // the edited message, rewinding the UI immediately.
      let cut = state.messages.length;
      for (let i = state.messages.length - 1; i >= 0; i--) {
        if (state.messages[i].role === "user") {
          cut = i;
          break;
        }
      }
      const userMsg: ChatMessage = {
        id: optId(state.idSeq),
        role: "user",
        text: action.text,
        attachments: action.attachments?.length ? action.attachments : undefined,
      };
      const messages = [...state.messages.slice(0, cut), userMsg];
      return startTurn(reset(state, { idSeq: state.idSeq + 1 }), messages);
    }

    case "sendAccepted":
      return reset(state, { status: "running", runId: action.runId, stopped: false });

    case "stopOptimistic":
      return reset(state, { status: "idle" });

    case "stop": {
      const flushed = foldStoppedTail(state);
      return reset(state, {
        status: "idle",
        runId: null,
        stopped: true,
        messages: flushed.messages,
        stream: "",
        thinking: "",
        statusText: "",
        idSeq: flushed.idSeq,
      });
    }

    case "invalidate":
      return reset(state, { stopped: true, runId: null });

    case "clearStream":
      return reset(state, { stream: "", thinking: "", statusText: "" });

    case "agentEvent":
      return applyAgentEvent(state, action.event);

    case "loaded":
      return isInFlight(state) || state.stopped
        ? reset(state, { messages: mergeCommitted(state.messages, action.rows) })
        : reset(state, { messages: action.rows, stream: "", thinking: "", statusText: "", hasNewBelow: false });

    case "externalRunning":
      return state.externalRunning === action.running
        ? state
        : reset(state, { externalRunning: action.running });

    case "reconcile": {
      // Only a run we believe is actively streaming can be reconciled down. While
      // status==="sending" the backend may still be spinning up the worker thread
      // (slow listener ping / skill build before it returns a run_id), so an empty
      // running-list means "not started", never "died". The wrapping hook only
      // dispatches this after a run has gone silent AND a confirming backend check,
      // so a single running===false is decisive here.
      if (state.status !== "running" || action.running) return state;
      return reset(state, {
        status: "idle",
        runId: null,
        stream: "",
        thinking: "",
        statusText: "",
        reloadToken: state.reloadToken + 1,
      });
    }

    case "atBottom":
      {
        const hasNewBelow = action.atBottom ? false : state.hasNewBelow;
        if (state.atBottom === action.atBottom && state.hasNewBelow === hasNewBelow) {
          return state;
        }
        return reset(state, { atBottom: action.atBottom, hasNewBelow });
      }

    default:
      return state;
  }
}
