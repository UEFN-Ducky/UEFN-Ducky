import {

  createContext,

  useCallback,

  useContext,

  useEffect,

  useMemo,

  useRef,

  useState,

  type ReactNode,

} from "react";

import type { ChatTab, EditorTab, FolderItem } from "../types/panel";

import type { QuickOpenMode } from "../components/quick-open/quickOpenUtils";

import { QuickOpenPalette } from "../components/quick-open/QuickOpenPalette";

import {

  registerQuickOpenOpener,

  type QuickOpenLaunchOptions,

} from "../components/quick-open/quickOpenGlobal";



export interface QuickOpenHandlers {

  openTabs: EditorTab[];

  folders: FolderItem[];

  rootChats: FolderItem["chats"];

  onOpenFile: (path: string, name: string) => void;

  onOpenChat: (chat: ChatTab) => void;

}



interface QuickOpenBridgeValue {

  handlers: QuickOpenHandlers | null;

  registerHandlers: (handlers: QuickOpenHandlers | null) => void;

  paletteOpen: boolean;

  mode: QuickOpenMode;

  seedQuery: string | undefined;

  openPalette: (opts?: QuickOpenMode | QuickOpenLaunchOptions) => void;

  closePalette: () => void;

  setMode: (mode: QuickOpenMode) => void;

}



const QuickOpenBridgeContext = createContext<QuickOpenBridgeValue | null>(null);



function normalizeOpenOptions(opts?: QuickOpenMode | QuickOpenLaunchOptions): QuickOpenLaunchOptions {

  if (!opts) return { mode: "file" };

  if (typeof opts === "string") return { mode: opts };

  return opts;

}



export function QuickOpenBridgeProvider({ children }: { children: ReactNode }) {

  const handlersRef = useRef<QuickOpenHandlers | null>(null);

  const [handlers, setHandlers] = useState<QuickOpenHandlers | null>(null);

  const [paletteOpen, setPaletteOpen] = useState(false);

  const [mode, setMode] = useState<QuickOpenMode>("file");

  const [seedQuery, setSeedQuery] = useState<string | undefined>(undefined);



  const registerHandlers = useCallback((next: QuickOpenHandlers | null) => {

    handlersRef.current = next;

    setHandlers(next);

  }, []);



  const openPalette = useCallback((opts?: QuickOpenMode | QuickOpenLaunchOptions) => {

    const next = normalizeOpenOptions(opts);

    setMode(next.mode ?? "file");

    setSeedQuery(next.query);

    setPaletteOpen(true);

  }, []);



  const closePalette = useCallback(() => {

    setPaletteOpen(false);

    setMode("file");

    setSeedQuery(undefined);

  }, []);



  useEffect(() => {

    registerQuickOpenOpener(openPalette);

    return () => registerQuickOpenOpener(null);

  }, [openPalette]);



  useEffect(() => {

    const onKeyDown = (e: KeyboardEvent) => {

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "p" && !e.shiftKey) {

        e.preventDefault();

        openPalette({ mode: "file" });

        return;

      }

      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "p") {

        e.preventDefault();

        openPalette({ mode: "command", query: ">" });

        return;

      }

      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") {

        e.preventDefault();

        openPalette({ mode: "symbol", query: "@" });

        return;

      }

      if (e.key === "F1") {

        e.preventDefault();

        openPalette({ mode: "command", query: ">" });

      }

    };

    window.addEventListener("keydown", onKeyDown);

    return () => window.removeEventListener("keydown", onKeyDown);

  }, [openPalette]);



  const value = useMemo(

    () => ({

      handlers,

      registerHandlers,

      paletteOpen,

      mode,

      seedQuery,

      openPalette,

      closePalette,

      setMode,

    }),

    [handlers, registerHandlers, paletteOpen, mode, seedQuery, openPalette, closePalette],

  );



  return (

    <QuickOpenBridgeContext.Provider value={value}>

      {children}

      <QuickOpenPalette />

    </QuickOpenBridgeContext.Provider>

  );

}



export function useQuickOpenBridge(): QuickOpenBridgeValue {

  const ctx = useContext(QuickOpenBridgeContext);

  if (!ctx) throw new Error("useQuickOpenBridge must be used within QuickOpenBridgeProvider");

  return ctx;

}



export function useRegisterQuickOpenHandlers(handlers: QuickOpenHandlers | null) {

  const { registerHandlers } = useQuickOpenBridge();

  useEffect(() => {

    registerHandlers(handlers);

    return () => registerHandlers(null);

  }, [handlers, registerHandlers]);

}


