type PyWebViewBridge = {
  platform?: string;
};

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
