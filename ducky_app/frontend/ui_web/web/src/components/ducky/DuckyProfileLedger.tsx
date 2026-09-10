import { useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import type { ChatTab } from "../../types/panel";
import { ChangesView } from "../changes/ChangesView";

type Props = {
  profileId: string;
  profileName: string;
};

/** Island changeset ledger for every current chat of this ducky type. */
export function DuckyProfileLedger({ profileId, profileName }: Props) {
  const [chats, setChats] = useState<ChatTab[]>([]);

  useEffect(() => {
    let cancelled = false;
    const api = getApi();
    if (!api?.list_all_conversations) return;
    void api.list_all_conversations().then((rows) => {
      if (cancelled || !Array.isArray(rows)) return;
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
    return () => {
      cancelled = true;
    };
  }, [profileId, profileName]);

  return (
    <div className="ducky-profile-ledger">
      <p className="ducky-profile-tab-hint">
        File and editor changes from every current {profileName || "ducky"} chat — toggle Archive
        to see locked history.
      </p>
      <ChangesView
        profileId={profileId}
        profileName={profileName}
        hideDuckyFilter
        allChats={chats}
      />
    </div>
  );
}
