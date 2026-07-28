import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Modal } from "./Modal";
import type { MessageAttachmentDto } from "../types/panel";

interface AttachmentPreviewModalProps {
  open: boolean;
  attachment: MessageAttachmentDto | null;
  onClose: () => void;
  /** When provided (composer previews), enables drawing on images; called with the annotated PNG data URL. */
  onSaveDrawing?: (dataUrl: string) => void;
}

function attachmentImageSrc(att: MessageAttachmentDto): string | null {
  if (att.kind !== "image" || !att.data_base64) return null;
  const mime = att.mime || "image/png";
  return `data:${mime};base64,${att.data_base64}`;
}

/** Map a pointer position to canvas pixel coordinates (canvas is CSS-scaled over the image). */
export function mapPointerToCanvas(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  canvasWidth: number,
  canvasHeight: number,
): { x: number; y: number; scale: number } {
  return {
    x: ((clientX - rect.left) / rect.width) * canvasWidth,
    y: ((clientY - rect.top) / rect.height) * canvasHeight,
    scale: canvasWidth / rect.width,
  };
}

export function AttachmentPreviewModal({ open, attachment, onClose, onSaveDrawing }: AttachmentPreviewModalProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [color, setColor] = useState("#ff3b30");
  const [brush, setBrush] = useState(4);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!open) {
      setDrawing(false);
      setDirty(false);
    }
  }, [open, attachment]);

  // Size the overlay canvas to the image's natural pixels once drawing starts.
  useEffect(() => {
    if (!drawing) return;
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas) return;
    const apply = () => {
      canvas.width = img.naturalWidth || img.width;
      canvas.height = img.naturalHeight || img.height;
    };
    if (img.complete) apply();
    else img.addEventListener("load", apply, { once: true });
  }, [drawing]);

  if (!attachment) return null;

  const imageSrc = attachmentImageSrc(attachment);
  const title = attachment.name || (attachment.kind === "image" ? "Image" : "File");
  const canDraw = attachment.kind === "image" && !!imageSrc && !!onSaveDrawing;

  const strokeTo = (e: ReactPointerEvent<HTMLCanvasElement>, begin: boolean) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pt = mapPointerToCanvas(e.clientX, e.clientY, canvas.getBoundingClientRect(), canvas.width, canvas.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = brush * pt.scale;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const from = begin ? pt : (lastPointRef.current ?? pt);
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(pt.x, pt.y);
    ctx.stroke();
    lastPointRef.current = { x: pt.x, y: pt.y };
    setDirty(true);
  };

  const clearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setDirty(false);
  };

  const saveDrawing = () => {
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas || !onSaveDrawing) return;
    const out = document.createElement("canvas");
    out.width = canvas.width;
    out.height = canvas.height;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, out.width, out.height);
    ctx.drawImage(canvas, 0, 0);
    onSaveDrawing(out.toDataURL("image/png"));
    onClose();
  };

  return (
    <Modal open={open} onClose={onClose} title={title} width={900}>
      <div className="attachment-preview-body">
        {canDraw && (
          <div className="attachment-draw-toolbar">
            {!drawing ? (
              <button type="button" className="settings-btn" onClick={() => setDrawing(true)}>
                Draw
              </button>
            ) : (
              <>
                <input
                  type="color"
                  className="attachment-draw-color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  aria-label="Brush color"
                />
                <input
                  type="range"
                  className="attachment-draw-size"
                  min={1}
                  max={24}
                  value={brush}
                  onChange={(e) => setBrush(Number(e.target.value))}
                  aria-label="Brush size"
                />
                <button type="button" className="settings-btn" onClick={clearCanvas} disabled={!dirty}>
                  Clear
                </button>
                <button
                  type="button"
                  className="settings-btn"
                  onClick={() => {
                    clearCanvas();
                    setDrawing(false);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="settings-btn modal-confirm-btn"
                  onClick={saveDrawing}
                  disabled={!dirty}
                >
                  Save drawing
                </button>
              </>
            )}
          </div>
        )}
        {attachment.kind === "image" && imageSrc ? (
          <div className="attachment-draw-wrap">
            <img ref={imgRef} src={imageSrc} alt={attachment.name} className="attachment-preview-image" />
            {drawing && (
              <canvas
                ref={canvasRef}
                className="attachment-draw-canvas"
                onPointerDown={(e) => {
                  if (e.button !== 0) return;
                  e.currentTarget.setPointerCapture(e.pointerId);
                  lastPointRef.current = null;
                  strokeTo(e, true);
                }}
                onPointerMove={(e) => {
                  if (lastPointRef.current) strokeTo(e, false);
                }}
                onPointerUp={() => {
                  lastPointRef.current = null;
                }}
              />
            )}
          </div>
        ) : attachment.kind === "file" ? (
          <pre className="attachment-preview-file selectable-text">{attachment.text || ""}</pre>
        ) : (
          <p className="attachment-preview-missing">Preview unavailable.</p>
        )}
      </div>
    </Modal>
  );
}
