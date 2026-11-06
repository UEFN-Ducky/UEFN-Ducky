import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { useChatsChanged } from "../../hooks/useChatsChanged";
import type { ChatTab } from "../../types/panel";

// Lazy like the other ChangesView hosts, so the ledger stays out of the boot chunk.
const ChangesView = lazy(() => import("../changes/ChangesView").then((m) => ({ default: m.ChangesView })));

type Props = {
  profileId: string;
  profileName: string;
};

/** Island changeset ledger for every current chat of this ducky type. */
export function DuckyProfileLedger({ profileId, profileName }: Props) {
  const [chats, setChats] = useState<ChatTab[]>([]);
  const seqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = ++seqRef.current;
    const api = getApi();
    if (!api?.list_all_conversations) return;
    await api.list_all_conversations().then((rows) => {
      if (seq !== seqRef.current || !Array.isArray(rows)) return;
      const pid = profileId.trim();
      const name = profileName.trim().toLowerCase();
      setChats(
        rows
          .filter((row) => {
            const rowPid = String(row.profile_id || "").trim();
            const rowName = String(row.ducky_name || "").trim().toLowerCase();
            if (pid && rowPid === pid) return true;
            return Boolean(name) && rowName === name;
          })
          .map((row) => ({
            id: row.id,
            name: row.title || row.ducky_name || row.id,
            duckyName: row.ducky_name,
            duckyStyle: row.ducky_style,
            profileId: String(row.profile_id || ""),
            model: row.model,
          })),
      );
    });
  }, [profileId, profileName]);

  useEffect(() => {
    void load();
    return () => {
      seqRef.current++;
    };
  }, [load]);
  // Chats come and go (and the launcher's start-up copy is refreshed with chats_changed).
  useChatsChanged(load);

  return (
    <div className="ducky-profile-ledger">
      <p className="ducky-profile-tab-hint">
        File and editor changes from every current {profileName || "ducky"} chat — toggle Archive
        to see locked history.
      </p>
      <Suspense fallback={null}>
        <ChangesView
          profileId={profileId}
          profileName={profileName}
          hideDuckyFilter
          allChats={chats}
        />
      </Suspense>
    </div>
  );
}
