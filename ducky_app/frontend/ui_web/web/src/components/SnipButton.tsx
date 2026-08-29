import { useState } from "react";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import { captureSnipFile } from "./snipCapture";

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
    if (busy) return;
    setBusy(true);
    try {
      const snip = await captureSnipFile();
      if (snip) onCaptured(snip.file, { projectPath: snip.projectPath });
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
