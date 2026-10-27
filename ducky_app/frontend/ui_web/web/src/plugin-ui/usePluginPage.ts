import { useEffect, useMemo, useState } from "react";
import { assetUrl } from "../remote/assetBase";
import { getDirectTransport } from "../remote/directTransport";
import { buildRemotePluginPage, pluginPageSource } from "./remotePluginPage";

export type PluginPage = {
  /** What the frame loads; null while a linked page is still being built (or failed). */
  src: string | null;
  /** True when the page comes over the PC link (the phone panel, another Ducky's window). */
  linked: boolean;
  error: string;
};

/**
 * The URL a plugin frame loads. The desktop serves the page itself; with a PC link the
 * page is built from the desktop's files over the link (remotePluginPage), since a
 * sandboxed frame never goes through the panel's Service Worker and a plain URL would hit
 * the site. Every plugin frame (tabs, Settings embeds, file panes) goes through here.
 *
 * ``query`` (``?file=…``) reaches the desktop's request only: a linked page is a blob:
 * document, whose ``location.search`` is empty.
 */
export function usePluginPage(pluginId: string, entry: string | undefined, version?: string | number, query = ""): PluginPage {
  const source = useMemo(() => {
    if (!pluginId || !entry) return null;
    const got = pluginPageSource(pluginId, entry, !!getDirectTransport(), assetUrl);
    return got.kind === "local" ? { ...got, url: got.url + query } : { ...got, path: got.path + query };
  }, [pluginId, entry, query]);
  const [built, setBuilt] = useState<{ path: string; url: string; error: string } | null>(null);
  const linkPath = source?.kind === "link" ? source.path : "";
  useEffect(() => {
    const transport = getDirectTransport();
    if (!linkPath || !transport) return;
    let gone = false;
    let revoke = () => {};
    setBuilt(null);
    buildRemotePluginPage(linkPath, (p) => transport.fetchBlob(p)).then(
      (page) => {
        if (gone) {
          page.revoke();
          return;
        }
        revoke = page.revoke;
        setBuilt({ path: linkPath, url: page.url, error: "" });
      },
      (err: unknown) => {
        if (!gone) setBuilt({ path: linkPath, url: "", error: String((err as Error)?.message || err) });
      },
    );
    return () => {
      gone = true;
      revoke();
    };
  }, [linkPath, version]);
  if (!source) return { src: null, linked: false, error: "" };
  if (source.kind === "local") return { src: source.url, linked: false, error: "" };
  const mine = built?.path === linkPath ? built : null;
  return { src: mine?.url || null, linked: true, error: mine?.error || "" };
}
