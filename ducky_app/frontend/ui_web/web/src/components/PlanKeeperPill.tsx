import { useEffect, useState } from "react";
import type { ChatPlan } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { isPlanFinished, isPlanPaused } from "../utils/planLock";

type KeeperState = "off" | "watching" | "waiting" | "parked";

const POLL_MS = 30_000;

const LABEL: Record<Exclude<KeeperState, "off">, string> = {
  watching: "Keeper on",
  waiting: "Keeper waiting",
  parked: "Keeper parked",
};

const TIP: Record<Exclude<KeeperState, "off">, string> = {
  watching: "The team keeper wakes this plan's lead when nothing moves for 2 minutes. Pause stops it.",
  waiting: "The team keeper is waiting while work or a workflow runs. Pause stops it.",
  parked: "The team keeper woke the lead enough times; it waits until the plan changes. Pause stops it.",
};

/** The Plan bar's keeper pill: is the team keeper looking after this plan, and Pause/Resume it. */
export function PlanKeeperPill({ plan }: { plan: ChatPlan }) {
  const lead = (plan.chat_id || "").trim();
  const paused = isPlanPaused(plan);
  const finished = isPlanFinished(plan);
  const [state, setState] = useState<KeeperState>("off");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!lead || paused || finished) {
      setState("off");
      return;
    }
    let live = true;
    const load = async () => {
      try {
        const res = await getApi()?.team_keeper_status?.(lead);
        if (live && res?.state) setState(res.state);
      } catch {
        // Keep the last state; the next poll tries again.
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [lead, paused, finished]);

  const setPlayback = async (status: "paused" | "open") => {
    const api = getApi();
    if (!api?.update_plan || busy) return;
    setBusy(true);
    try {
      // The plan_updated event that follows refreshes the bar.
      await api.update_plan(lead, undefined, "", "", "", true, status, null);
    } finally {
      setBusy(false);
    }
  };

  if (finished || !lead) return null;
  if (paused) {
    return (
      <button
        type="button"
        className="chat-plan-popup-keeper is-paused"
        onClick={() => void setPlayback("open")}
        disabled={busy}
        title="This plan is paused: the team keeper leaves it alone. Resume to let it keep the team going."
      >
        Paused · Resume
      </button>
    );
  }
  if (state === "off") return null;
  return (
    <button
      type="button"
      className={`chat-plan-popup-keeper is-${state}`}
      onClick={() => void setPlayback("paused")}
      disabled={busy}
      title={TIP[state]}
    >
      <span className="chat-plan-popup-keeper-dot" aria-hidden />
      {LABEL[state]} · Pause
    </button>
  );
}
