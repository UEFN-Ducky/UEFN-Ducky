import { useLayoutEffect, useRef, useState } from "react";

import { ContextMenu, type ContextMenuItem } from "../ContextMenu";
import { Icons } from "../../icons/Icons";

interface EditorTabBarMenuProps {
  hasDirtyTabs: boolean;
  groupLocked?: boolean;
  onSaveAll?: () => void;
  onCloseAll?: () => void;
  onCloseSaved?: () => void;
  onToggleGroupLock?: () => void;
}

export function EditorTabBarMenu({
  hasDirtyTabs,
  groupLocked = false,
  onSaveAll,
  onCloseAll,
  onCloseSaved,
  onToggleGroupLock,
}: EditorTabBarMenuProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ x: number; y: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) {
      setMenuPos(null);
      return;
    }
    const rect = buttonRef.current.getBoundingClientRect();
    const panelW = 220;
    let left = rect.right - panelW;
    if (left < 8) left = 8;
    setMenuPos({ x: left, y: rect.bottom + 4 });
  }, [open]);

  const items: ContextMenuItem[] = [
    {
      id: "save-all",
      label: "Save All",
      disabled: !hasDirtyTabs || !onSaveAll,
      onClick: onSaveAll,
    },
    { id: "sep-1", label: "", separator: true },
    {
      id: "close-all",
      label: "Close All",
      onClick: onCloseAll,
    },
    {
      id: "close-saved",
      label: "Close Saved",
      onClick: onCloseSaved,
    },
    { id: "sep-2", label: "", separator: true },
    {
      id: "lock-group",
      label: "Lock Group",
      checked: groupLocked,
      keepOpen: true,
      disabled: !onToggleGroupLock,
      onClick: onToggleGroupLock,
    },
  ];

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`icon-btn no-drag editor-tab-bar-menu-btn${groupLocked ? " is-locked" : ""}`}
        title="Tab actions"
        aria-label="Tab actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((prev) => !prev);
        }}
      >
        {groupLocked ? <Icons.Lock /> : <Icons.MoreHorizontal />}
      </button>
      {open && menuPos ? (
        <ContextMenu x={menuPos.x} y={menuPos.y} items={items} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}
