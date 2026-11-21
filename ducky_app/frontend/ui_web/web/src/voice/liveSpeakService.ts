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
import { shouldNarrateToolResult, speakableThinkingLine } from "./processNarration";
import { pullSentences } from "./sentenceQueue";
import { loadLastAssistantText, prepareSpokenText, summarizeForSpeech } from "./speakReply";
import { needsLlmSummary, spokenToolResult } from "./toolNarration";
import { ttsEngine, type TtsVoiceInfo } from "./ttsEngine";
import { getVoiceSettings } from "./voiceSettings";
import { clearWorkStatus, endWorkTurn, recordToolDone, recordToolStart } from "./workStatus";

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
  /** Group chats: the member writing the open reply, read in its own voice. */
  replyAuthor: MessageAuthorDto | null;
  /** Members with no voice of their own, and the installed voice each was given. */
  memberVoices: Map<string, string>;
};

let installedVoices: TtsVoiceInfo[] = [];

const sessions = new Map<string, LiveSession>();
let micOwner: string | null = null;
let unsubEvents: (() => void) | null = null;
let unsubTts: (() => void) | null = null;
let unsubTransport: (() => void) | null = null;

function ensureBus() {
  if (unsubEvents) return;
  if (!installedVoices.length) {
    void Promise.resolve(ttsEngine.whenVoicesReady?.())
      .then((voices) => { if (Array.isArray(voices) && voices.length) installedVoices = voices; })
      .catch(() => {});
  }
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
  const state = getLiveVoiceState(session.chatId);
  // Narration must not cover a disconnected mic or erase its actionable error.
  if ((state.status === "connecting" || state.status === "error") && patch.status && patch.status !== "error") {
    const rest = { ...patch };
    delete rest.status;
    delete rest.error;
    patchLiveVoiceState(session.chatId, rest);
    return;
  }
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

/** A member with no voice set still sounds like itself: an installed voice other than the
 *  chat's, a different one per member while they last, the same one each time. */
function memberVoice(session: LiveSession, author?: MessageAuthorDto | null): string {
  if (author?.tts_voice) return author.tts_voice;
  const id = author?.member_conv_id || "";
  if (!id) return session.voiceId;
  const given = session.memberVoices.get(id);
  if (given) return given;
  const pool = installedVoices.map((v) => v.id).filter((v) => v && v !== session.voiceId);
  if (!pool.length) return session.voiceId;
  let hash = 0;
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const used = new Set(session.memberVoices.values());
  const start = hash % pool.length;
  const pick = pool.map((_, i) => pool[(start + i) % pool.length]).find((v) => !used.has(v)) ?? pool[start];
  session.memberVoices.set(id, pick);
  return pick;
}

/** A group member speaks in its own voice; the chat's voice is everyone else's. */
function speakOpts(session: LiveSession, author?: MessageAuthorDto | null) {
  return {
    chatId: session.chatId,
    speaker: author?.name || session.speakerName || "Ducky",
    voiceId: memberVoice(session, author),
    rate: author?.tts_speed || session.speed,
  };
}

function eventAuthor(event: AgentEvent): MessageAuthorDto | null {
  const author = (event as { author?: unknown }).author;
  return author && typeof author === "object" ? (author as MessageAuthorDto) : null;
}

function enqueueAnswerSentences(session: LiveSession, force: boolean): void {
  const { sentences, remainder } = pullSentences(session.assistantText, force);
  session.assistantText = remainder;
  if (!sentences.length) return;
  session.streamedThisTurn = true;
  for (const line of sentences) {
    session.lastFlushedAnswer = line;
    enqueueAnswerSpeak(line, speakOpts(session, session.replyAuthor));
  }
}

function flushThinking(session: LiveSession, author: MessageAuthorDto | null = session.replyAuthor) {
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
  enqueueProcessSpeak(line, speakOpts(session, author));
}

function flushAssistant(session: LiveSession) {
  enqueueAnswerSentences(session, true);
}

function enqueueToolResult(session: LiveSession, tool: ToolCallData | undefined, author: MessageAuthorDto | null) {
  if (!shouldNarrateToolResult(getVoiceSettings().processTalk)) return;
  const digest = spokenToolResult(tool);
  if (tool && needsLlmSummary(tool) && tool.result) {
    const raw = tool.result;
    enqueueProcessSpeak(() => summarizeForSpeech(raw), speakOpts(session, author), digest);
    return;
  }
  enqueueProcessSpeak(digest, speakOpts(session, author));
}

function onAgentEvent(event: AgentEvent) {
  const convId = event.conv_id?.trim();
  if (!convId) return;
  const session = sessions.get(convId);
  if (!session) return;
  const author = eventAuthor(event);

  if (event.type === "text_delta") {
    const chunk = typeof event.text === "string" ? event.text : "";
    if (!chunk) return;
    if (!session.replyOpen) {
      session.replyOpen = true;
      session.streamedThisTurn = false;
      session.lastFlushedAnswer = "";
      session.replyAuthor = author;
    } else if (author && author.member_conv_id !== session.replyAuthor?.member_conv_id) {
      // Another member's reply: finish the last one in its own voice first.
      flushAssistant(session);
      session.replyAuthor = author;
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
      flushThinking(session, author ?? session.replyAuthor);
    }, 450);
    return;
  }

  if (event.type === "tool") {
    flushThinking(session);
    flushAssistant(session);
    const step = recordToolStart(session.chatId, event.tool, getVoiceSettings().processTalk);
    if (step.speak) {
      enqueueProcessSpeak(step.line, speakOpts(session, author ?? session.replyAuthor));
    }
    publish(session, { status: "thinking", error: "" });
    return;
  }

  if (event.type === "tool_done") {
    // A step whose start stayed quiet keeps its result quiet too.
    if (recordToolDone(session.chatId, event.tool)) {
      enqueueToolResult(session, event.tool, author ?? session.replyAuthor);
    }
    return;
  }

  if (event.type !== "assistant_done") return;

  if (event.note) {
    // A member's note posted to this group: read in its voice, leave any open reply alone.
    const noteText = typeof event.text === "string" ? event.text : "";
    void prepareSpokenText(noteText).then((spoken) => {
      if (!spoken || !sessions.has(session.chatId)) return;
      enqueueFinalSpeak(spoken, speakOpts(session, author));
      publish(session, { status: "speaking", spokenText: spoken, speakerName: author?.name || session.speakerName });
    }).catch(() => {});
    return;
  }

  // The reply's author speaks it; the chat's own voice and name stay the chat's.
  const speaker = author ?? session.replyAuthor;
  if (speaker && !session.replyAuthor) session.replyAuthor = speaker;
  flushThinking(session);
  flushAssistant(session);
  endWorkTurn(session.chatId);
  session.replyOpen = false;
  session.replyAuthor = null;
  session.speakingAfterDone = true;
  publish(session, { status: "thinking" });

  const eventText = typeof event.text === "string" ? event.text : "";

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
      publish(session, { spokenText: spoken, speakerName: speaker?.name || session.speakerName });
      enqueueFinalSpeak(spoken, speakOpts(session, speaker));
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
    replyAuthor: null,
    memberVoices: new Map(),
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
  clearWorkStatus(id);
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

/** Speak a voice-lane reply now, in the chat's voice. */
export function speakLiveReply(chatId: string, text: string): void {
  const session = sessions.get((chatId || "").trim());
  const line = (text || "").trim();
  if (!session || !line) return;
  session.speakingAfterDone = true;
  enqueueFinalSpeak(line, speakOpts(session, null));
  publish(session, { status: "speaking", spokenText: line });
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
