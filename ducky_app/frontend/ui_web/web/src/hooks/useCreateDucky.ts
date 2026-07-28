import { useCallback } from "react";
import { randomDuckyStyleFromCatalog, useDuckyCatalog } from "../components/ducky/DuckyCatalogContext";
import type { ChatTab, FolderItem } from "../types/panel";
import { numberedEntryName } from "../utils/numberedEntryName";
import { chatNamesInFolder } from "../utils/sidebarTree";
import { getApi } from "./usePanelApi";

export interface CreatedDucky {
  id: string;
  name: string;
  duckyStyle?: string;
  filePath?: string;
}

export function useCreateDucky({
  folders,
  rootChats,
  load,
  onCreated,
  filePath,
  selectedFolderId,
}: {
  folders: FolderItem[];
  rootChats: FolderItem["chats"];
  load: () => Promise<void>;
  onCreated: (chat: ChatTab) => void;
  filePath?: string;
  selectedFolderId?: string | null;
}) {
  const catalog = useDuckyCatalog();

  const createDucky = useCallback(async (): Promise<CreatedDucky | null> => {
    const api = getApi();
    if (!api) return null;

    const folderId = selectedFolderId ?? "";
    const siblingNames = chatNamesInFolder(folders, folderId, rootChats);
    const title = numberedEntryName("NewDucky", siblingNames);

    const styleId = randomDuckyStyleFromCatalog(catalog);
    const normFile = filePath?.replace(/\\/g, "/");
    const conv = await api.create_conversation(folderId, styleId, normFile);
    if (conv.title !== title) {
      await api.rename_conversation(conv.id, title);
    }

    const created: CreatedDucky = {
      id: conv.id,
      name: title,
      duckyStyle: conv.ducky_style || styleId,
      filePath: conv.file_path?.replace(/\\/g, "/") || normFile,
    };
    onCreated({
      id: created.id,
      name: created.name,
      duckyStyle: created.duckyStyle,
      filePath: created.filePath,
    });
    await load();
    return created;
  }, [catalog, filePath, folders, load, onCreated, rootChats, selectedFolderId]);

  return { createDucky };
}
