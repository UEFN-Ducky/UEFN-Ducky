/**
 * Branded Allow/Block for microphone — replaces the WebView chrome prompt UX.
 */

import { useEffect, useState } from "react";

import { Modal, ModalActions } from "../components/Modal";
import { Icons } from "../icons/Icons";
import {
  resolveMicPermissionPrompt,
  subscribeMicPermissionPrompt,
} from "./micPermission";

export function MicPermissionModal() {
  const [open, setOpen] = useState(false);

  useEffect(() => subscribeMicPermissionPrompt(setOpen), []);

  return (
    <Modal
      open={open}
      onClose={() => resolveMicPermissionPrompt(false)}
      title="Use your microphone"
      width={420}
      footer={
        <ModalActions
          onCancel={() => resolveMicPermissionPrompt(false)}
          onConfirm={() => resolveMicPermissionPrompt(true)}
          cancelLabel="Block"
          confirmLabel="Allow"
        />
      }
    >
      <div className="mic-permission-body">
        <div className="mic-permission-icon" aria-hidden>
          <Icons.Mic />
        </div>
        <p className="mic-permission-text">
          UEFN Ducky needs the microphone for dictation and live voice chat. You can change this
          anytime in Settings → Audio.
        </p>
      </div>
    </Modal>
  );
}
