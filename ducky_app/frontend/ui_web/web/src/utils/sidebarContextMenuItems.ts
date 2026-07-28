import type { ContextMenuItem } from "../components/ContextMenu";

export function contextMenuSeparator(id: string): ContextMenuItem {
  return { id, label: "", separator: true };
}

export function showHiddenProjectFilesItem(
  showHidden: boolean,
  onToggle: (value: boolean) => void | Promise<void>,
): ContextMenuItem {
  return {
    id: "show-hidden-project-files",
    label: "Show hidden project files",
    checked: showHidden,
    keepOpen: true,
    onClick: () => void onToggle(!showHidden),
  };
}

export function fileTreeCreateItems(
  onNewFolder: () => void,
  onNewVerse: () => void,
  onNewFile: () => void,
): ContextMenuItem[] {
  return [
    { id: "new-folder", label: "New Folder", onClick: onNewFolder },
    { id: "new-verse", label: "New Verse class", onClick: onNewVerse },
    { id: "new-file", label: "New File", onClick: onNewFile },
  ];
}

export function fileTreeClipboardItems(
  onCopy: () => void,
  onCut: () => void,
  onPaste: () => void,
  canModify: boolean,
  canPaste: boolean,
): ContextMenuItem[] {
  return [
    { id: "copy", label: "Copy", disabled: !canModify, onClick: onCopy },
    { id: "cut", label: "Cut", disabled: !canModify, onClick: onCut },
    { id: "paste", label: "Paste", disabled: !canPaste, onClick: onPaste },
  ];
}

export function fileTreeRevealItems(
  onRevealInSidebar: () => void,
  onRevealInFileExplorer: () => void,
): ContextMenuItem[] {
  return [
    { id: "reveal-sidebar", label: "Reveal in Explorer", onClick: onRevealInSidebar },
    { id: "reveal-os", label: "Reveal in File Explorer", onClick: onRevealInFileExplorer },
  ];
}

export function duckyTreeCreateItems(
  onNewDucky: () => void,
  onNewGroup?: () => void,
): ContextMenuItem[] {
  const items: ContextMenuItem[] = [
    { id: "new-ducky", label: "New Ducky", onClick: onNewDucky },
  ];
  if (onNewGroup) {
    items.unshift({ id: "new-group", label: "New Group Chat", onClick: onNewGroup });
  }
  return items;
}

export function duckyTreeCompactItem(
  compact: boolean,
  onToggle: (value: boolean) => void,
): ContextMenuItem {
  return {
    id: "ducky-compact",
    label: "Compact",
    switch: true,
    checked: compact,
    keepOpen: true,
    onClick: () => onToggle(!compact),
  };
}
