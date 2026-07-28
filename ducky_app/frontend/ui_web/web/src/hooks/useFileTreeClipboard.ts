import { useCallback, useState } from "react";

export type ClipboardMode = "copy" | "cut";
export type FileClipboard = { mode: ClipboardMode; paths: string[] } | null;

/** In-memory file-tree clipboard for copy/cut/paste (paste is done by the tree). */
export function useFileTreeClipboard() {
  const [clipboard, setClipboard] = useState<FileClipboard>(null);

  const copy = useCallback((paths: string[]) => {
    if (paths.length) setClipboard({ mode: "copy", paths: [...paths] });
  }, []);
  const cut = useCallback((paths: string[]) => {
    if (paths.length) setClipboard({ mode: "cut", paths: [...paths] });
  }, []);
  const clear = useCallback(() => setClipboard(null), []);

  return { clipboard, copy, cut, clear };
}
