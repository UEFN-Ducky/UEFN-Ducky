import { useEffect, useState } from "react";

import { getLiveVoiceChatIds, subscribeLiveVoiceChats } from "./liveChats";

function snapshotLiveIds(): Set<string> {
  return new Set(getLiveVoiceChatIds());
}

/** True while this chat is in live-voice mode. Re-renders on start/stop. */
export function useIsLiveChat(chatId: string): boolean {
  const id = (chatId || "").trim();
  const [live, setLive] = useState(() => Boolean(id && getLiveVoiceChatIds().has(id)));
  useEffect(() => {
    const sync = () => setLive(Boolean(id && getLiveVoiceChatIds().has(id)));
    sync();
    return subscribeLiveVoiceChats(sync);
  }, [id]);
  return live;
}

/** Live chat ids for list/tab strips (one subscription for N rows). */
export function useLiveChatIds(): ReadonlySet<string> {
  const [ids, setIds] = useState<ReadonlySet<string>>(snapshotLiveIds);
  useEffect(() => subscribeLiveVoiceChats(() => setIds(snapshotLiveIds())), []);
  return ids;
}
