import { useEffect, useState } from "react";
import { readLiveOverview } from "../hooks/useLiveOverview";
import { getApi } from "../hooks/usePanelApi";
import { subscribePanelPush } from "../hooks/usePanelPushBus";
import { Modal, ModalActions } from "./Modal";

type Invite = { account: string; space: string; room: string; from: string; expires: number };

/** Seconds left on an invite, from its expiry (epoch seconds) and now (ms). */
export function secondsLeft(expires: number, nowMs: number): number {
  return Math.max(0, Math.ceil(expires - nowMs / 1000));
}

/**
 * A teammate invited this PC into their live group (live.py `roster_invites`): Join or Not
 * now. The question goes by itself when the invite runs out (30 s); a newer invite replaces it.
 */
export function LiveInvitePrompt() {
  const [invite, setInvite] = useState<Invite | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");

  useEffect(
    () =>
      subscribePanelPush((event) => {
        if (event.type !== "live_invite") return;
        setError("");
        setNow(Date.now());
        setInvite({
          account: event.account ?? "",
          space: event.space ?? "",
          room: event.room ?? "",
          from: event.from || "A teammate",
          expires: Number(event.expires ?? 0),
        });
      }),
    [],
  );

  const left = invite ? secondsLeft(invite.expires, now) : 0;
  useEffect(() => {
    if (!invite) return;
    if (left <= 0) {
      setInvite(null);
      return;
    }
    const timer = window.setTimeout(() => setNow(Date.now()), 1000);
    return () => window.clearTimeout(timer);
  }, [invite, left]);

  if (!invite) return null;
  const join = async () => {
    const out = await getApi()?.live_team_join?.(invite.account, invite.space, invite.room);
    if (out && out.ok === false) {
      setError(out.error || "Couldn't join.");
      return;
    }
    setInvite(null);
    void readLiveOverview();
  };

  return (
    <Modal
      open
      onClose={() => setInvite(null)}
      title="Live invite"
      width={360}
      closeOnBackdrop={false}
      footer={<ModalActions onCancel={() => setInvite(null)} onConfirm={() => void join()} cancelLabel="Not now" confirmLabel={`Join (${left})`} />}
    >
      <p>{invite.from} invites you to go live.</p>
      {error ? <p role="alert">{error}</p> : null}
    </Modal>
  );
}
