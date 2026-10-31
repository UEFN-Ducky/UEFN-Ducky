import { useEffect, useRef, useState } from "react";
import { getApi } from "../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { ContextMenu, type ContextMenuItem } from "./ContextMenu";

/** One row of WebView2's own menu over typed text (Paste...), or a spelling fix: `replace`
 * is typed over the selected misspelled word. */
export type NativeMenuRow = { id?: number; label?: string; enabled?: boolean; separator?: boolean; replace?: string };

type OpenMenu = { token: string; x: number; y: number; rows: NativeMenuRow[] };

/** WebView2's rows as Ducky menu items; a pick answers with the row's command id. */
export function nativeMenuItems(rows: NativeMenuRow[], onPick: (commandId: number) => void): ContextMenuItem[] {
  return rows.map((row, i) =>
    row.separator
      ? { id: `sep-${i}`, label: "", separator: true }
      : {
          id: `cmd-${row.id}`,
          label: row.label || "",
          disabled: row.enabled === false,
          onClick: () => {
            // insertText keeps undo and fires the box's input event, like typing the fix.
            if (row.replace) document.execCommand("insertText", false, row.replace);
            onPick(Number(row.id));
          },
        },
  );
}

/**
 * Right-click on text you can edit (the chat box, any field): the browser's spell
 * checker suggestions and its Cut / Copy / Paste, in Ducky's menu. The app sends
 * WebView2's own menu here (webview_native_menu.py); the pick goes back and WebView2
 * runs it, so the fix is the spell checker's, never a word list of ours.
 */
export function NativeTextMenu() {
  const [menu, setMenu] = useState<OpenMenu | null>(null);
  const lastPoint = useRef({ x: 0, y: 0 });
  const answered = useRef("");

  useEffect(() => {
    const note = (e: MouseEvent) => {
      lastPoint.current = { x: e.clientX, y: e.clientY };
    };
    window.addEventListener("contextmenu", note, true);
    installPanelPushBus();
    const stop = subscribePanelPush((event) => {
      const e = event as { type?: string; token?: string; items?: NativeMenuRow[] };
      if (e.type !== "native_menu" || !e.token || !Array.isArray(e.items)) return;
      answered.current = "";
      setMenu({ token: e.token, rows: e.items, ...lastPoint.current });
    });
    return () => {
      window.removeEventListener("contextmenu", note, true);
      stop();
    };
  }, []);

  if (!menu) return null;
  const answer = (commandId: number) => {
    if (answered.current === menu.token) return;
    answered.current = menu.token;
    void getApi()?.native_menu_pick?.(menu.token, commandId);
  };
  return (
    <ContextMenu
      x={menu.x}
      y={menu.y}
      keepFocus
      items={nativeMenuItems(menu.rows, answer)}
      onClose={() => {
        answer(-1);
        setMenu(null);
      }}
    />
  );
}
