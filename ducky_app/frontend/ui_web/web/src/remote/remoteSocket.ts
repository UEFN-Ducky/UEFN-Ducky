/**
 * Terminal and Verse LSP sessions talk to the desktop over local WebSockets
 * (`ws://127.0.0.1:<port>`). On a phone those addresses do not exist; in
 * direct mode the same traffic rides a `stream:<id>` DataChannel and the
 * desktop bridges it to the local socket. Callers get a WebSocket-shaped
 * object either way.
 */
import { getDirectTransport, type DirectStreamSocket } from "./directTransport";

export type RemoteSocket = Pick<WebSocket, "send" | "close" | "readyState"> & {
  onopen: ((ev?: unknown) => void) | null;
  onmessage: ((ev: { data: string | ArrayBuffer }) => void) | null;
  onclose: ((ev: { code: number; reason: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  binaryType: "arraybuffer" | "blob";
};

const LOCAL_WS = /^ws:\/\/(127\.0\.0\.1|localhost):\d+/;

export function openRemoteSocket(url: string, kind: "terminal" | "lsp"): RemoteSocket {
  const direct = getDirectTransport();
  if (direct && LOCAL_WS.test(url)) {
    return direct.openStream(kind, url) as unknown as DirectStreamSocket as unknown as RemoteSocket;
  }
  return new WebSocket(url) as unknown as RemoteSocket;
}
