type PyWebViewBridge = {
  platform?: string;
  _jsApiCallback?: (funcName: string, params: unknown, id: string) => void;
};

export type ResizeEdge = "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se";

/** Bypass the async PanelApi worker and route input to the originating HWND. */
export function beginNativeWindowResize(edge: ResizeEdge, doubleClick = false): boolean {
  const pw = (window as unknown as { pywebview?: PyWebViewBridge }).pywebview;
  if (!pw?._jsApiCallback) return false;
  pw._jsApiCallback("uefnNativeWindowResize", [edge, doubleClick], "resize");
  return true;
}

/** True when the OS handles window edge resize / snap (pywebview WebView2 on Windows). */
export function isNativeWindowChrome(): boolean {
  const pw = (window as unknown as { pywebview?: PyWebViewBridge }).pywebview;
  return pw?.platform === "edgechromium";
}

export function markNativeWindowChromeBody(): void {
  if (isNativeWindowChrome()) {
    document.body.classList.add("native-window-chrome");
  }
}
