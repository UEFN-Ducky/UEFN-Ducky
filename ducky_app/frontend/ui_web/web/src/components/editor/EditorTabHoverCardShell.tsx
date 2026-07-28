import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import {
  useEditorTabHoverCard,
  type EditorTabHoverCardPlacement,
} from "../../hooks/useEditorTabHoverCard";
import { EDITOR_TAB_HOVER_CARD_EST_HEIGHT } from "../../hooks/editorTabHoverCardPosition";
import { ScopedCss, useScopedClass } from "../../utils/scopedCss";

interface EditorTabHoverCardShellProps {
  disabled?: boolean;
  placement?: EditorTabHoverCardPlacement;
  /** Used to clamp vertical position — pass a tight estimate for short cards. */
  cardHeight?: number;
  children: ReactNode;
  card: ReactNode;
}

export function EditorTabHoverCardShell({
  disabled = false,
  placement = "below",
  cardHeight = EDITOR_TAB_HOVER_CARD_EST_HEIGHT,
  children,
  card,
}: EditorTabHoverCardShellProps) {
  const scopeClass = useScopedClass("editor-tab-hover-card");
  const { anchorRef, open, pos, scheduleShow, scheduleHide, cancelHide } =
    useEditorTabHoverCard(disabled, placement, cardHeight);

  return (
    <>
      <span
        ref={anchorRef}
        className={
          placement === "right" || placement === "left"
            ? "editor-tab-hover-anchor editor-tab-hover-anchor--sidebar"
            : "editor-tab-hover-anchor"
        }
        onMouseEnter={scheduleShow}
        onMouseLeave={scheduleHide}
        onFocus={scheduleShow}
        onBlur={scheduleHide}
      >
        {children}
      </span>
      {open && pos
        ? createPortal(
            <>
              <ScopedCss
                selector={`.${scopeClass}`}
                rules={{
                  "--editor-tab-hover-left": `${pos.left}px`,
                  "--editor-tab-hover-top": `${pos.top}px`,
                }}
              />
              <div
                className={`editor-tab-hover-card no-drag ${scopeClass}`}
                role="tooltip"
                onMouseEnter={cancelHide}
                onMouseLeave={scheduleHide}
              >
                {card}
              </div>
            </>,
            document.body,
          )
        : null}
    </>
  );
}
