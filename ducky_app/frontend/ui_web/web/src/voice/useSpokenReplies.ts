import { useEffect, useRef } from "react";

import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getLiveVoiceChatIds } from "./liveChats";
import { speakReply } from "./speakReply";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings, loadVoiceSettings, subscribeVoiceSettings } from "./voiceSettings";

/**
 * App-shell hook: on assistant_done, speak a short summary when voice is enabled
 * and the chat is not in live voice mode (live mode handles its own speak).
 */
export function useSpokenReplies(opts?: {
  /** Optional per-chat voice override lookup. */
  voiceForChat?: (chatId: string) => string | undefined;
  /** Optional per-chat talking-speed override lookup. */
  speedForChat?: (chatId: string) => number | undefined;
}) {
  const voiceForChatRef = useRef(opts?.voiceForChat);
  voiceForChatRef.current = opts?.voiceForChat;
  const speedForChatRef = useRef(opts?.speedForChat);
  speedForChatRef.current = opts?.speedForChat;

  useEffect(() => {
    void loadVoiceSettings();
    return subscribeVoiceSettings(() => {
      const s = getVoiceSettings();
      ttsEngine.setVoice(s.defaultVoice);
      ttsEngine.setRate(s.defaultSpeed);
    });
  }, []);

  useEffect(() => {
    installAgentEventBus();
    return subscribeAgentEvents((event) => {
      if (event.type !== "assistant_done") return;
      const convId = event.conv_id?.trim();
      if (!convId) return;
      const settings = getVoiceSettings();
      if (!settings.enabled) return;
      if (getLiveVoiceChatIds().has(convId)) return;
      if (settings.spokenStyle === "speak_along") {
        ttsEngine.flush();
        return;
      }
      void speakReply(convId, voiceForChatRef.current?.(convId), {
        speed: speedForChatRef.current?.(convId),
      });
    });
  }, []);
}
