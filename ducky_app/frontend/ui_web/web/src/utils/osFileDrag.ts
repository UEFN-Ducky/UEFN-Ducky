/** Sentinel reported via set_import_drop_target while an OS file is dragged over the
 * editor (or the empty state): a drop then OPENS the file(s) editable in place instead
 * of copying into Content. Mirrors OPEN_EXTERNAL_TARGET in file_drop_import.py. */
export const OPEN_EXTERNAL_TARGET = ":open-external:";

/** Sentinel while an OS file is dragged over a chat composer/pane: JS attaches via
 * FileList; the native drop handler must no-op (empty dest would otherwise open). */
export const CHAT_ATTACH_TARGET = ":chat-attach:";

/** Attribute on chat panes that accept OS-file drops as message attachments. */
export const CHAT_ATTACH_DROP_ATTR = "data-chat-attach-drop";

/** True when the event target sits inside a chat attach drop zone. */
export function isChatAttachDropTarget(target: EventTarget | null): boolean {
  const el = target as { closest?: (sel: string) => unknown } | null;
  if (!el || typeof el.closest !== "function") return false;
  return Boolean(el.closest(`[${CHAT_ATTACH_DROP_ATTR}]`));
}

/** True for a real OS/Explorer file drag. Internal dnd-kit tree drags are pointer-based
 * and editor-tab drags carry custom/text MIME types, so neither trips this. */
export function dragHasOsFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  for (const t of dt.types) if (t === "Files") return true;
  return false;
}
