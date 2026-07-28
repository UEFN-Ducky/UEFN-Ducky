export const EDITOR_TAB_HOVER_CARD_WIDTH = 260;
/** Default max card height for vertical clamp (chat cards with actions/meta). */
export const EDITOR_TAB_HOVER_CARD_EST_HEIGHT = 280;
/** Compact file-row cards — over-estimating shoves UEFN Core rows way above the hover target. */
export const EDITOR_TAB_HOVER_CARD_FILE_EST_HEIGHT = 96;

export type EditorTabHoverCardPlacement = "below" | "right" | "left";

type RectLike = Pick<DOMRect, "left" | "right" | "top" | "bottom">;

/** Pure layout for editor/sidebar hover cards — prefers `placement`, flips beside when clipped. */
export function computeEditorTabHoverCardPosition(
  rect: RectLike,
  placement: EditorTabHoverCardPlacement,
  viewport: { width: number; height: number },
  cardHeight = EDITOR_TAB_HOVER_CARD_EST_HEIGHT,
): { left: number; top: number } {
  const gap = 6;
  const edge = 8;
  const width = EDITOR_TAB_HOVER_CARD_WIDTH;
  const height = Math.max(48, cardHeight);

  if (placement === "below") {
    const left = Math.max(edge, Math.min(rect.left, viewport.width - width - edge));
    return { left, top: rect.bottom + gap };
  }

  const preferRight = placement === "right";
  const fitsRight = rect.right + gap + width <= viewport.width - edge;
  const fitsLeft = rect.left - gap - width >= edge;
  // Prefer requested side when it fits; otherwise flip; if neither fits, pick the roomier side.
  const placeRight = preferRight
    ? fitsRight || (!fitsLeft && viewport.width - rect.right >= rect.left)
    : !(fitsLeft || (!fitsRight && rect.left > viewport.width - rect.right));

  const left = placeRight
    ? Math.min(rect.right + gap, viewport.width - width - edge)
    : Math.max(edge, rect.left - width - gap);
  // Align with the row; only nudge up when the card would clip past the bottom edge.
  const top = Math.max(edge, Math.min(rect.top, viewport.height - height - edge));
  return { left, top };
}
