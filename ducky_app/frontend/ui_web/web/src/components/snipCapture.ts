import { getApi } from "../hooks/usePanelApi";

export interface SnipResult {
  file: File;
  projectPath?: string;
}

/** Runs the Windows region snipper and turns the reply into a PNG File. */
export async function captureSnipFile(): Promise<SnipResult | null> {
  const api = getApi();
  if (!api?.snip_screen) return null;
  const res = await api.snip_screen();
  if (!res?.ok || !res.data_base64) return null;
  const bin = atob(res.data_base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const name = res.name || `snip-${stamp}.png`;
  return {
    file: new File([bytes], name, { type: "image/png" }),
    projectPath: (res.path || "").trim() || undefined,
  };
}
