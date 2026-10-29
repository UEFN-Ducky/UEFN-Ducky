import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatPlan, PlanProgress } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { useAgentEventSubscription } from "../hooks/useAgentEventBus";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { ChatPlanPopup } from "./ChatPlanPopup";
import { PlanLeadLinks } from "./PlanOwnerChips";
import { countProgress } from "./PlanTodoCard";

/** Live plan state shared by the chat dock and its finished-plan card. */
export function useChatPlan(chatId: string) {
  const [chatPlan, setChatPlan] = useState<ChatPlan | null>(null);
  const [chatPlanProgress, setChatPlanProgress] = useState<PlanProgress | null>(null);
  const request = useRef(0);
  const reloadPlan = useCallback(async () => {
    const version = ++request.current;
    const api = getApi();
    if (!api?.get_plan) return;
    try {
      const res = await api.get_plan(chatId);
      if (version !== request.current) return;
      setChatPlan(res.plan ?? null);
      setChatPlanProgress(res.progress ?? null);
    } catch {
      // Keep the last resolved view until a later event can refresh it.
    }
  }, [chatId]);

  useEffect(() => {
    setChatPlan(null);
    setChatPlanProgress(null);
    void reloadPlan();
    return () => { request.current += 1; };
  }, [reloadPlan]);

  useAgentEventSubscription(chatId, (event) => {
    if (event.conv_id !== chatId) return;
    if (event.type === "plan_assignment_changed") void reloadPlan();
    if (event.type === "plan_updated") {
      if (chatPlan?.assigned_from?.chat_id) {
        void reloadPlan();
      } else {
        request.current += 1;
        setChatPlan(event.plan ?? null);
        setChatPlanProgress(event.progress ?? null);
      }
    }
  }, [reloadPlan, chatPlan?.assigned_from?.chat_id]);

  const sourceChatId = chatPlan?.assigned_from?.chat_id || "";
  useAgentEventSubscription(sourceChatId, (event) => {
    if (!sourceChatId || sourceChatId === chatId || event.conv_id !== sourceChatId) return;
    if (event.type === "plan_updated" || event.type === "plan_assignment_changed") void reloadPlan();
  }, [chatId, reloadPlan]);

  return { chatPlan, chatPlanProgress, setChatPlan, setChatPlanProgress };
}

/** True when the chat has no plan, or every step of it is finished. */
export function usePlanAllDone(chatPlan: ChatPlan | null, chatPlanProgress: PlanProgress | null): boolean {
  return useMemo(() => {
    if (!chatPlan) return true;
    if (chatPlanProgress) {
      const { done, total } = countProgress(chatPlan, chatPlanProgress);
      return total > 0 && done >= total;
    }
    const todos = chatPlan.todos || [];
    return todos.length > 0 && todos.every((t) => t.status === "completed" || t.status === "cancelled");
  }, [chatPlan, chatPlanProgress]);
}

type PlanSetters = {
  setChatPlan: (plan: ChatPlan | null) => void;
  setChatPlanProgress: (progress: PlanProgress | null) => void;
};

/** A plan tool finished in this chat: take its plan, or load it when the event has none. */
export function applyPlanToolDone(
  event: { plan?: ChatPlan | null; progress?: PlanProgress | null },
  chatId: string,
  { setChatPlan, setChatPlanProgress }: PlanSetters,
) {
  // Prefer the plan payload from the event (member plans in a group).
  if (event.plan) {
    setChatPlan(event.plan);
    setChatPlanProgress(event.progress ?? null);
    return;
  }
  const api = getApi();
  if (api?.get_plan) {
    void api
      .get_plan(chatId)
      .then((res) => {
        setChatPlan(res.plan ?? null);
        setChatPlanProgress(res.progress ?? null);
      })
      .catch(() => {
        /* keep existing plan state */
      });
  }
}

/** Open the chat's plan in a tab, or stop tracking it (deletes the plan). */
export function useChatPlanActions({
  chatId,
  chatName,
  chatPlan,
  onOpenPlan,
  setChatPlan,
  setChatPlanProgress,
}: PlanSetters & {
  chatId: string;
  chatName?: string;
  chatPlan: ChatPlan | null;
  onOpenPlan?: (chatId: string, title?: string) => void;
}) {
  const { confirm } = useConfirmModal();
  const handleOpenPlanTab = useCallback(() => {
    // In group chats, the visible plan may belong to a member (plan.chat_id).
    const planChatId = (chatPlan?.chat_id || "").trim() || chatId;
    onOpenPlan?.(planChatId, chatPlan?.title || chatName || "Plan");
  }, [onOpenPlan, chatId, chatName, chatPlan?.chat_id, chatPlan?.title]);

  const handleStopTrackingPlan = useCallback(async () => {
    const title = chatPlan?.title || "Plan";
    const planChatId = (chatPlan?.chat_id || "").trim() || chatId;
    if (
      !(await confirm({
        message: `Stop tracking “${title}”? Removes the plan from this chat (plan file deleted). The chat stays.`,
        confirmLabel: "Stop tracking",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_plan) return;
    const res = await api.delete_plan(planChatId);
    if (!res.ok) return;
    setChatPlan(null);
    setChatPlanProgress(null);
  }, [chatId, chatPlan?.chat_id, chatPlan?.title, confirm, setChatPlan, setChatPlanProgress]);

  return { handleOpenPlanTab, handleStopTrackingPlan };
}

export interface ChatPlanCardProps {
  plan: ChatPlan | null;
  progress: PlanProgress | null;
  allDone: boolean;
  onOpenPlan: () => void;
  onStopTracking: () => void | Promise<void>;
  onOpenFile: (path: string, name: string, options?: { line?: number }) => void;
}

/** The plan docked above the composer while the chat's plan still has open steps. */
export function ChatPlanCard({ plan, progress, allDone, onOpenPlan, onStopTracking, onOpenFile }: ChatPlanCardProps) {
  if (plan == null || allDone) return null;
  return (
    <div className="chat-pane-plan-dock">
      <ChatPlanPopup
        plan={plan}
        // A member's part of a team plan: Lead and Full plan sit inside the opened panel.
        header={plan.assigned_from ? <PlanLeadLinks assignedFrom={plan.assigned_from} /> : null}
        progress={progress}
        onOpenPlan={onOpenPlan}
        onStopTracking={onStopTracking}
        onOpenFile={onOpenFile}
      />
    </div>
  );
}
