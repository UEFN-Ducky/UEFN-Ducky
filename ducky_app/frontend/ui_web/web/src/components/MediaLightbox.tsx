/**
 * Big view of a picture, video, sound or chart from the chat: a tool card's media or a
 * chart in a reply. Arrows (and ← →) step through the items it was opened with.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Icons } from "../icons/Icons";
import { Modal } from "./Modal";

export type LightboxItem = {
  kind: "image" | "video" | "audio" | "graphic" | string;
  name: string;
  url?: string;
  /** A chart or SVG to draw big (kind "graphic"). */
  node?: ReactNode;
};

interface MediaLightboxProps {
  items: LightboxItem[];
  /** Index to show; null = closed. */
  index: number | null;
  onClose: () => void;
}

export function MediaLightbox({ items, index, onClose }: MediaLightboxProps) {
  const [at, setAt] = useState(index ?? 0);
  useEffect(() => {
    if (index !== null) setAt(index);
  }, [index]);
  const count = items.length;
  const open = index !== null && count > 0;
  useEffect(() => {
    if (!open || count < 2) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") setAt((i) => (i + 1) % count);
      else if (e.key === "ArrowLeft") setAt((i) => (i - 1 + count) % count);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, count]);
  if (!open) return null;
  const item = items[Math.min(at, count - 1)];
  return (
    <Modal open onClose={onClose} title={item.name || "Preview"} width={1200} className="media-lightbox" bodyClassName="media-lightbox-body">
      <div className="media-lightbox-stage">
        {item.kind === "image" && item.url ? <img src={item.url} alt={item.name} draggable={false} /> : null}
        {item.kind === "video" && item.url ? <video key={item.url} src={item.url} controls autoPlay preload="metadata" /> : null}
        {item.kind === "audio" && item.url ? <audio key={item.url} src={item.url} controls autoPlay preload="metadata" /> : null}
        {item.kind === "graphic" ? <div className="media-lightbox-graphic">{item.node}</div> : null}
        {count > 1 ? (
          <>
            <button type="button" className="media-lightbox-step media-lightbox-step--prev" aria-label="Previous" onClick={() => setAt((i) => (i - 1 + count) % count)}>
              <Icons.ChevronLeft />
            </button>
            <button type="button" className="media-lightbox-step media-lightbox-step--next" aria-label="Next" onClick={() => setAt((i) => (i + 1) % count)}>
              <Icons.ChevronRight />
            </button>
          </>
        ) : null}
      </div>
      <div className="media-lightbox-foot">
        {count > 1 ? <span>{at + 1} / {count}</span> : <span />}
        {item.url ? (
          <a href={item.url} download={item.name} className="media-lightbox-save">
            <Icons.Download />
            <span>Save</span>
          </a>
        ) : null}
      </div>
    </Modal>
  );
}

/** A chart or SVG with an enlarge button in its corner that opens it in the lightbox. */
export function EnlargeableGraphic({ name, children }: { name: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="enlargeable-graphic">
      {children}
      <button type="button" className="enlargeable-graphic-button" title="Open big" aria-label="Open big" onClick={() => setOpen(true)}>
        <Icons.FullScreen />
      </button>
      <MediaLightbox items={[{ kind: "graphic", name, node: children }]} index={open ? 0 : null} onClose={() => setOpen(false)} />
    </div>
  );
}
