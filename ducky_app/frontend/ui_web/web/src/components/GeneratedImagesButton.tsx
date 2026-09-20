import { useEffect, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import type { MessageAttachmentDto } from "../types/panel";
import { useUiTarget } from "../ui-targets/registry";

interface GeneratedImagesButtonProps {
  disabled?: boolean;
  onPick: (attachment: MessageAttachmentDto) => void;
}

export function GeneratedImagesButton({ disabled, onPick }: GeneratedImagesButtonProps) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Array<{ name: string; media_url?: string; prompt?: string }>>([]);
  const uiTargetRef = useUiTarget("chat.composer.generated", {
    kind: "button",
    label: "Generated",
    route: "chat",
  });

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void getApi()
      ?.list_generated_images?.()
      ?.then((r) => {
        if (!cancelled && r?.ok) setRows(r.images || []);
      })
      ?.catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);

  const pick = async (name: string) => {
    const got = await getApi()?.get_generated_image_attachment?.(name);
    if (got?.ok && got.attachment) {
      onPick(got.attachment);
      setOpen(false);
    }
  };

  return (
    <div className="generated-images-btn-wrap">
      <button
        ref={uiTargetRef}
        type="button"
        className="snip-btn"
        title="Attach a generated image"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="8.5" cy="8.5" r="1.5" />
          <path d="M21 15l-5-5L5 21" />
        </svg>
      </button>
      {open ? (
        <div className="generated-images-flyout">
          {rows.length === 0 ? (
            <div className="generated-images-empty">No generated images yet</div>
          ) : (
            rows.map((row) => (
              <button
                key={row.name}
                type="button"
                className="generated-images-row"
                onClick={() => void pick(row.name)}
              >
                {row.media_url ? <img src={row.media_url} alt="" /> : null}
                <span>{row.prompt || row.name}</span>
              </button>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
