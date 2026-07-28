import { useEffect } from "react";
import { dragHasOsFiles } from "../utils/osFileDrag";

/**
 * Stop WebView2 from handing an OS-file drop to Windows.
 *
 * When a file is dragged from Explorer and dropped anywhere the app doesn't
 * explicitly handle it (the editor, a chat, empty chrome, Welcome/Settings),
 * WebView2's default drop action opens/navigates to the file — which pops the
 * Windows "Select an app to open this file" shell dialog and can blank the
 * panel. This installs a window-wide capture-phase guard that calls
 * `preventDefault` on file drags so the OS never gets the drop — Ducky keeps
 * acting as the text/image editor instead of deferring to Acrobat/Notepad/etc.
 *
 * It only calls `preventDefault` — never `stopPropagation` — so the Content
 * file tree's own drop handlers and pywebview's native `document` drop handler
 * (which reads `pywebviewFullPath` to copy files into the project) still run.
 */
export function useOsFileDropGuard(): void {
  useEffect(() => {
    const suppress = (e: DragEvent) => {
      if (!dragHasOsFiles(e.dataTransfer)) return;
      e.preventDefault();
      // Keep a copy cursor so the user sees the drop is accepted by Ducky.
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    // Capture phase so we win regardless of where the drop lands; dragover must
    // be cancelled too or the drop event never fires and the default proceeds.
    // Also bind document so drops during early load / outside #root still stay in-app.
    const opts: AddEventListenerOptions = { capture: true };
    window.addEventListener("dragover", suppress, opts);
    window.addEventListener("drop", suppress, opts);
    document.addEventListener("dragover", suppress, opts);
    document.addEventListener("drop", suppress, opts);
    return () => {
      window.removeEventListener("dragover", suppress, opts);
      window.removeEventListener("drop", suppress, opts);
      document.removeEventListener("dragover", suppress, opts);
      document.removeEventListener("drop", suppress, opts);
    };
  }, []);
}
