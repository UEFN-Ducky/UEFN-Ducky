import { getApi, isRemote } from "../hooks/usePanelApi";

function copyViaExecCommand(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok;
}

/** Copy text in the panel. WebView2 often ignores navigator.clipboard.writeText. */
export async function copyText(text: string): Promise<boolean> {
  const api = getApi();
  if (!isRemote() && api?.copy_text) {
    try {
      if (await api.copy_text(text)) return true;
    } catch {
      /* fall through */
    }
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* WebView2 / denied */
  }
  try {
    return copyViaExecCommand(text);
  } catch {
    return false;
  }
}
