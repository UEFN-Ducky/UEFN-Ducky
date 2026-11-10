/**
 * A short muted, looping "meet your first ducky" clip above the welcome ducky's first message,
 * until the person closes it (remembered: it never comes back). No clip bundled: nothing shows
 * and the first-run tour (welcomeDucky.ts) is the walkthrough, as before.
 */
import { useState } from "react";

// ponytail: empty slot until the owner records the 20-30 s clip from the real app; then
// `import clip from "../assets/welcome-ducky.mp4"` and use it here.
export const WELCOME_CLIP_SRC = "";

const KEY = "ducky.welcomeClip.v1";

type Saved = { chatId?: string; dismissed?: boolean };

function read(): Saved {
  try {
    return (JSON.parse(localStorage.getItem(KEY) || "{}") as Saved) || {};
  } catch {
    return {};
  }
}

function write(saved: Saved): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(saved));
  } catch {
    /* storage off: the card just shows again next time */
  }
}

/** The setup made this chat the welcome ducky: the clip shows at its top. */
export function rememberWelcomeChat(chatId: string): void {
  const saved = read();
  if (!saved.dismissed) write({ ...saved, chatId });
}

export function WelcomeClip({ chatId, src = WELCOME_CLIP_SRC }: { chatId: string; src?: string }) {
  const [saved, setSaved] = useState(read);
  if (!src || saved.dismissed || saved.chatId !== chatId) return null;
  const close = () => {
    const next = { ...saved, dismissed: true };
    write(next);
    setSaved(next);
  };
  return (
    <div className="welcome-clip" role="region" aria-label="Meet your first ducky">
      <video className="welcome-clip-video" src={src} muted loop autoPlay playsInline />
      <button type="button" className="welcome-clip-close" onClick={close}>
        Close
      </button>
    </div>
  );
}
