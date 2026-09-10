import { getApi } from "../../hooks/usePanelApi";
import { copyText } from "../../utils/copyText";

export async function copySupportDump(): Promise<boolean> {
  const api = getApi();
  if (!api?.copy_support_dump) return false;
  const text = await api.copy_support_dump();
  if (!text) return false;
  return copyText(text);
}
