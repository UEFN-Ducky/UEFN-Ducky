import { useEffect, useState } from "react";

import { AskUserModal } from "./AskUserModal";
import {
  getAskUserSession,
  settleAskUser,
  subscribeAskUser,
  type AskUserSession,
} from "./runAskUser";
import type { AskUserResult } from "./types";

/**
 * Mount once in App — true orphan ask_user only (no open/focused chat).
 * Chat-scoped asks dock above that chat's composer.
 */
export function AskUserHost() {
  const [session, setSession] = useState<AskUserSession | null>(() => getAskUserSession());

  useEffect(() => {
    return subscribeAskUser(() => setSession(getAskUserSession()));
  }, []);

  const onComplete = (result: AskUserResult) => {
    if (!session) return;
    settleAskUser(result, session.id);
  };

  return (
    <AskUserModal
      open={!!session}
      questions={session?.questions ?? []}
      title={session?.title ?? ""}
      queueAhead={session?.queueAhead ?? 0}
      sessionId={session?.id}
      onComplete={onComplete}
    />
  );
}
