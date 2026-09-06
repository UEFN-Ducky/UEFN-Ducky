import { getApi } from "../../hooks/usePanelApi";

export async function copySupportDump(): Promise<boolean> {
  const api = getApi();
  if (!api?.copy_support_dump) return false;
  const text = await api.copy_support_dump();
  if (!text) return false;
  await navigator.clipboard.writeText(text);
  return true;
}
