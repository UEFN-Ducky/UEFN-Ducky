import { useEffect, useState } from "react";

/** Ephemeral status text that auto-clears after `ms` (default 2.8s). */
export function useTimedMessage(ms = 2800): [string, (msg: string) => void] {
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), ms);
    return () => window.clearTimeout(timer);
  }, [message, ms]);

  return [message, setMessage];
}
