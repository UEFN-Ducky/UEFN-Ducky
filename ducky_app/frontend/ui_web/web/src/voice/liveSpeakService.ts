/**
 * Module-level live-voice sessions. Outlives ChatPane so a closed tab
 * keeps narrating until the user turns live off.
 */

import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import type { AgentEvent, MessageAuthorDto, ToolCallData } from "../types/panel";
import { getLiveVoiceState, patchLiveVoiceState, setLiveVoiceChat } from "./liveChats";
import {
  clearLiveSpeakQueueForChat,
  enqueueAnswerSpeak,
  enqueueFinalSpeak,
  enqueueProcessSpeak,
  getCurrentSpokenLine,
  getLiveSpeakTransport,
  liveSpeakQueueLength,
  parkLiveSpeakQueue,
  subscribeLiveSpeakTransport,
} from "./liveSpeakQueue";
import { shouldNarrateTool, shouldNarrateToolResult, speakableThinkingLine } from "./processNarration";
import { pullSentences } from "./sentenceQueue";
import { loadLastAssistantText, prepareSpokenText, summarizeForSpeech } from "./speakReply";
import { needsLlmSummary, spokenToolResult, spokenToolStart } from "./toolNarration";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings } from "./voiceSettings";

export type LiveChatOpts = {
  voiceId: string;
  speed: number;
  isGroup?: boolean;
  speakerName?: string;
};

type LiveSession = {
  chatId: string;
  voiceId: string;
  speed: number;
  isGroup: boolean;
  speakerName: string;
  assistantText: string;
  lastFlushedAnswer: string;
  thinkingBurst: string;
  thinkingTimer: number | null;
  lastThinkingAt: number;
  speakingAfterDone: boolean;
  /** Assistant is still streaming — do not return to listening between sentences. */
  replyOpen: boolean;
  streamedThisTurn: boolean;
};

const sessions = new Map<string, LiveSession>();
let micOwner: string | null = null;
let unsubEvents: (() => void) | null = null;
let unsubTts: (() => void) | null = null;
let unsubTransport: (() => void) | null = null;

function ensureBus() {
  if (unsubEvents) return;
  installAgentEventBus();
  unsubEvents = subscribeAgentEvents(onAgentEvent);
  unsubTts = ttsEngine.onStateChange(onTtsState);
  unsubTransport = subscribeLiveSpeakTransport(publishBehind);
}

function tearDownBusIfIdle() {
  if (sessions.size > 0) return;
  unsubEvents?.();
  unsubEvents = null;
  unsubTts?.();
  unsubTts = null;
  unsubTransport?.();
  unsubTransport = null;
}

function publish(session: LiveSession, patch: Parameters<typeof patchLiveVoiceState>[1]) {
  patchLiveVoiceState(session.chatId, patch);
}

function publishBehind() {
  const { behind } = getLiveSpeakTransport();
  for (const session of sessions.values()) {
    const current = getCurrentSpokenLine();
    const mine = current?.chatId === session.chatId;
    publish(session, {
      nextSpeaker: behind > 0 ? `${behind} behind` : "",
      speakerName: mine ? current?.speaker || session.speakerName : session.speakerName,
    });
  }
}

function speakOpts(session: LiveSession, speaker?: string) {
  return {
    chatId: session.chatId,
    speaker: speaker || session.speakerName || "Ducky",
    voiceId: session.voiceId,
    rate: session.speed,
  };
}

function enqueueAnswerSentences(session: LiveSession, force: boolean): void {
  const { sentences, remainder } = pullSentences(session.assistantText, force);
  session.assistantText = remainder;
  if (!sentences.length) return;
  session.streamedThisTurn = true;
  for (const line of sentences) {
    session.lastFlushedAnswer = line;
    enqueueAnswerSpeak(line, speakOpts(session));
  }
}

function flushThinking(session: LiveSession) {
  if (session.thinkingTimer != null) {
    window.clearTimeout(session.thinkingTimer);
    session.thinkingTimer = null;
  }
  const burst = session.thinkingBurst.trim();
  session.thinkingBurst = "";
  if (!burst) return;
  const now = Date.now();
  if (now - session.lastThinkingAt < 8000) return;
  const line = speakableThinkingLine(burst, getVoiceSettings().processTalk);
  if (!line) return;
  session.lastThinkingAt = now;
  enqueueProcessSpeak(line, speakOpts(session));
}

function flushAssistant(session: LiveSession) {
  enqueueAnswerSentences(session, true);
}

function enqueueToolResult(session: LiveSession, tool: ToolCallData | undefined) {
  if (!shouldNarrateToolResult(getVoiceSettings().processTalk)) return;
  const digest = spokenToolResult(tool);
  if (tool && needsLlmSummary(tool) && tool.result) {
    const raw = tool.result;
    enqueueProcessSpeak(() => summarizeForSpeech(raw), speakOpts(session), digest);
    return;
  }
  enqueueProcessSpeak(digest, speakOpts(session));
}

function onAgentEvent(event: AgentEvent) {
  const convId = event.conv_id?.trim();
  if (!convId) return;
  const session = sessions.get(convId);
  if (!session) return;

  if (event.type === "text_delta") {
    const chunk = typeof event.text === "string" ? event.text : "";
    if (!chunk) return;
    if (!session.replyOpen) {
      session.replyOpen = true;
      session.streamedThisTurn = false;
      session.lastFlushedAnswer = "";
    }
    session.assistantText += chunk;
    enqueueAnswerSentences(session, false);
    return;
  }

  if (event.type === "thinking") {
    const chunk = typeof event.text === "string" ? event.text : "";
    if (!chunk) return;
    session.thinkingBurst += chunk;
    if (session.thinkingTimer != null) return;
    session.thinkingTimer = window.setTimeout(() => {
      session.thinkingTimer = null;
      if (!sessions.has(session.chatId)) return;
      flushThinking(session);
    }, 450);
    return;
  }

  if (event.type === "tool") {
    flushThinking(session);
    flushAssistant(session);
    if (shouldNarrateTool(getVoiceSettings().processTalk)) {
      enqueueProcessSpeak(spokenToolStart(event.tool), speakOpts(session));
    }
    publish(session, { status: "thinking", error: "" });
    return;
  }

  if (event.type === "tool_done") {
    enqueueToolResult(session, event.tool);
    return;
  }

  if (event.type !== "assistant_done") return;

  flushThinking(session);
  flushAssistant(session);
  session.replyOpen = false;
  session.speakingAfterDone = true;
  publish(session, { status: "thinking" });

  const author = event.author as MessageAuthorDto | undefined;
  const eventText = typeof event.text === "string" ? event.text : "";
  if (author?.name) session.speakerName = author.name;
  if (author?.tts_voice) session.voiceId = author.tts_voice;
  if (author?.tts_speed) session.speed = author.tts_speed;

  if (session.streamedThisTurn) {
    session.streamedThisTurn = false;
    return;
  }

  void (async () => {
    if (!sessions.has(session.chatId)) return;
    try {
      const text = (eventText || "").trim() || (await loadLastAssistantText(convId));
      if (!sessions.has(session.chatId)) return;
      if (!text) {
        session.speakingAfterDone = false;
        publish(session, { status: "error", error: "empty reply" });
        return;
      }
      const spoken = await prepareSpokenText(text);
      if (!sessions.has(session.chatId)) return;
      if (!spoken) {
        session.speakingAfterDone = false;
        publish(session, { status: "error", error: "empty spoken text" });
        return;
      }
      if (spoken === session.lastFlushedAnswer) {
        return;
      }
      session.lastFlushedAnswer = spoken;
      publish(session, { spokenText: spoken, speakerName: session.speakerName });
      enqueueFinalSpeak(spoken, speakOpts(session, author?.name));
      publish(session, { status: "speaking", spokenText: spoken });
    } catch (err) {
      if (!sessions.has(session.chatId)) return;
      const msg = err instanceof Error ? err.message : String(err);
      session.speakingAfterDone = false;
      publish(session, { status: "error", error: msg || "Could not speak reply" });
    }
  })();
}

function onTtsState(s: "idle" | "speaking" | "paused") {
  const current = getCurrentSpokenLine();
  if (s === "speaking" && current) {
    const session = sessions.get(current.chatId);
    if (session) {
      publish(session, {
        status: "speaking",
        speakerName: current.speaker || session.speakerName,
        spokenText: typeof current.text === "string" ? current.text : current.resolvedText || "",
      });
    }
    return;
  }
  if (s !== "idle") return;
  for (const session of sessions.values()) {
    if (session.replyOpen) {
      publish(session, { status: liveSpeakQueueLength() > 0 ? "speaking" : "thinking" });
      continue;
    }
    if (!session.speakingAfterDone) {
      if (current?.chatId !== session.chatId) {
        publish(session, { status: "thinking" });
      }
      continue;
    }
    const remaining = liveSpeakQueueLength();
    if (remaining > 0) {
      publish(session, { status: "speaking" });
      continue;
    }
    session.speakingAfterDone = false;
    const idle = getLiveVoiceState(session.chatId).muted ? "muted" : "listening";
    publish(session, { status: idle, speakerName: "", nextSpeaker: "" });
  }
}

export function startLiveChat(chatId: string, opts: LiveChatOpts): void {
  const id = (chatId || "").trim();
  if (!id) return;
  const existing = sessions.get(id);
  if (existing) {
    existing.voiceId = opts.voiceId;
    existing.speed = opts.speed;
    existing.isGroup = Boolean(opts.isGroup);
    if (opts.speakerName) existing.speakerName = opts.speakerName;
    return;
  }
  sessions.set(id, {
    chatId: id,
    voiceId: opts.voiceId,
    speed: opts.speed,
    isGroup: Boolean(opts.isGroup),
    speakerName: opts.speakerName || "Ducky",
    assistantText: "",
    lastFlushedAnswer: "",
    thinkingBurst: "",
    thinkingTimer: null,
    lastThinkingAt: 0,
    speakingAfterDone: false,
    replyOpen: false,
    streamedThisTurn: false,
  });
  setLiveVoiceChat(id, true);
  patchLiveVoiceState(id, { status: "listening", error: "", muted: false });
  ensureBus();
}

export function stopLiveChat(chatId: string): void {
  const id = (chatId || "").trim();
  if (!id) return;
  const session = sessions.get(id);
  if (session?.thinkingTimer != null) window.clearTimeout(session.thinkingTimer);
  sessions.delete(id);
  clearLiveSpeakQueueForChat(id);
  if (micOwner === id) micOwner = null;
  setLiveVoiceChat(id, false);
  tearDownBusIfIdle();
}

export function isLiveChat(chatId: string): boolean {
  return sessions.has((chatId || "").trim());
}

export function updateLiveChatVoice(chatId: string, voiceId: string, speed: number): void {
  const session = sessions.get((chatId || "").trim());
  if (!session) return;
  session.voiceId = voiceId;
  session.speed = speed;
}

export function claimMic(chatId: string): boolean {
  const id = (chatId || "").trim();
  if (!id || !sessions.has(id)) return false;
  if (micOwner && micOwner !== id) return false;
  micOwner = id;
  return true;
}

export function releaseMic(chatId: string): void {
  const id = (chatId || "").trim();
  if (micOwner === id) micOwner = null;
}

export function micOwnerId(): string | null {
  return micOwner;
}

export function interruptLiveSpeak(): void {
  parkLiveSpeakQueue();
}

/** Test helper. */
export function _resetLiveSpeakService() {
  for (const session of sessions.values()) {
    if (session.thinkingTimer != null) window.clearTimeout(session.thinkingTimer);
  }
  sessions.clear();
  micOwner = null;
  unsubEvents?.();
  unsubEvents = null;
  unsubTts?.();
  unsubTts = null;
  unsubTransport?.();
  unsubTransport = null;
}
