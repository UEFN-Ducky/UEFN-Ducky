import { useState } from "react";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";

interface SnipButtonProps {
  disabled?: boolean;
  onCaptured: (file: File, meta?: { projectPath?: string }) => void;
}

/** Opens the Windows region snipper (Win+Shift+S) and drops the result into
 * the composer as an image attachment. */
export function SnipButton({ disabled, onCaptured }: SnipButtonProps) {
  const [busy, setBusy] = useState(false);
  if (!getApi()?.snip_screen) return null;

  const handleClick = async () => {
    const api = getApi();
    if (!api?.snip_screen || busy) return;
    setBusy(true);
    try {
      const res = await api.snip_screen();
      if (res?.ok && res.data_base64) {
        const bin = atob(res.data_base64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        const name = res.name || `snip-${stamp}.png`;
        const projectPath = (res.path || "").trim() || undefined;
        onCaptured(new File([bytes], name, { type: "image/png" }), { projectPath });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      className={`snip-btn${busy ? " snip-btn--busy" : ""}`}
      title="Snip a region of the screen into the chat"
      disabled={disabled || busy}
      onClick={() => void handleClick()}
    >
      <Icons.Crop />
    </button>
  );
}
