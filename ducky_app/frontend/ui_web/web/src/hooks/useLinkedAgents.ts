import { useCallback, useEffect, useState } from "react";
import type { AgentEvent, ChatMessage, ChatTab, LinkedAgent } from "../types/panel";
import { useAgentEventSubscription } from "./useAgentEventBus";
import { useRunningAgents } from "./useRunningAgents";
import { mergeLinkedAgents, parseLinkedAgentsFromMessages } from "../utils/linkedAgents";

export function useLinkedAgents(parentConvId: string, messages: ChatMessage[], allChats: ChatTab[]) {
  const [linkedAgents, setLinkedAgents] = useState<LinkedAgent[]>([]);
  const runningIds = useRunningAgents();

  useEffect(() => {
    const fromHistory = parseLinkedAgentsFromMessages(messages);
    if (fromHistory.length > 0) {
      setLinkedAgents((prev) => mergeLinkedAgents(prev, fromHistory));
    }
  }, [messages]);

  // The persisted parent link is authoritative. Events make the card appear
  // instantly, while this keeps it present after reopening/restarting and also
  // recovers an event that arrived before this chat pane mounted.
  useEffect(() => {
    const persisted: LinkedAgent[] = allChats
      .filter((chat) => chat.parentConvId === parentConvId)
      .map((chat) => ({
        childConvId: chat.id,
        title: chat.name,
        status: runningIds.has(chat.id) ? "running" : "done",
      }));
    if (persisted.length > 0) {
      setLinkedAgents((prev) => mergeLinkedAgents(prev, persisted));
    }
  }, [allChats, parentConvId, runningIds]);

  const handleEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type !== "linked_agent") return;
      if (event.parent_conv_id !== parentConvId || !event.child_conv_id) return;
      const incoming: LinkedAgent = {
        childConvId: event.child_conv_id,
        title: event.title ?? "Ducky",
        status: event.status ?? "running",
      };
      setLinkedAgents((prev) => {
        const placeholderIdx = prev.findIndex(
          (a) => !a.childConvId && a.title === incoming.title,
        );
        if (placeholderIdx >= 0) {
          const next = [...prev];
          next[placeholderIdx] = incoming;
          return mergeLinkedAgents([], next);
        }
        return mergeLinkedAgents(prev, [incoming]);
      });
    },
    [parentConvId],
  );

  useAgentEventSubscription(parentConvId, handleEvent, [handleEvent]);

  useEffect(() => {
    setLinkedAgents((prev) => {
      if (prev.length === 0) return prev;
      let changed = false;
      const next = prev.map((agent) => {
        if (runningIds.has(agent.childConvId)) {
          if (agent.status === "running") return agent;
          changed = true;
          return { ...agent, status: "running" as const };
        }
        if (agent.status === "running") {
          changed = true;
          return { ...agent, status: "done" as const };
        }
        return agent;
      });
      return changed ? next : prev;
    });
  }, [runningIds]);

  return linkedAgents;
}
