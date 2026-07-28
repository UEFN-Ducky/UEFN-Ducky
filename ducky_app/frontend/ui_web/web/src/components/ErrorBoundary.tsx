import { Component, type ErrorInfo, type ReactNode } from "react";
import { getApi } from "../hooks/usePanelApi";
import { skipTour } from "../walkthrough/WalkthroughService";

/** Public download page (Store block auto-pulls latest Setup). */
export const UEFN_DUCKY_DOWNLOAD_PAGE = "https://uefnducky.org/download";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Rendered instead of the default panel. Receives the error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** Short name of the region this guards ("File tree", "App") — shown in the default panel. */
  label?: string;
  /** When any value in this array changes, the boundary resets and re-renders its children.
   * Pass the filter query so a search-induced crash clears itself once the query changes. */
  resetKeys?: unknown[];
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
  componentStack: string;
  appVersion: string;
  installLocation: string;
  downloadUrl: string;
  detailsOpen: boolean;
}

function openExternal(url: string): void {
  try {
    const api = getApi();
    if (url === UEFN_DUCKY_DOWNLOAD_PAGE && typeof api?.open_download_page === "function") {
      void api.open_download_page();
      return;
    }
    if (typeof api?.open_external_url === "function") {
      void api.open_external_url(url);
      return;
    }
  } catch {
    // fall through
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Catches render/lifecycle throws in a subtree so one bad component can't blank the whole app.
 * Without this, an uncaught throw unmounts the entire React root to a blank screen. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = {
    error: null,
    componentStack: "",
    appVersion: "",
    installLocation: "",
    downloadUrl: UEFN_DUCKY_DOWNLOAD_PAGE,
    detailsOpen: false,
  };

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const componentStack = info.componentStack || "";
    console.error(`[ErrorBoundary${this.props.label ? ` · ${this.props.label}` : ""}]`, error, info);
    this.setState({ componentStack });
    this.props.onError?.(error, info);

    const api = getApi();
    void (async () => {
      let appVersion = "";
      let installLocation = "";
      let downloadUrl = UEFN_DUCKY_DOWNLOAD_PAGE;
      try {
        if (api?.get_version) appVersion = String((await api.get_version()) || "");
      } catch {
        /* ignore */
      }
      try {
        if (api?.get_install_info) {
          const infoDto = await api.get_install_info();
          installLocation = String(infoDto?.install_location || "");
        }
      } catch {
        /* ignore */
      }
      try {
        if (api?.get_app_update_status) {
          const st = await api.get_app_update_status();
          if (st?.download_url) downloadUrl = String(st.download_url);
          // Prefer Store installer URL when present (direct Setup).
          const installer = (st as { installer_url?: string | null })?.installer_url;
          if (installer) downloadUrl = String(installer);
          if (!appVersion && st?.local_version) appVersion = String(st.local_version);
        }
      } catch {
        /* ignore */
      }
      this.setState({ appVersion, installLocation, downloadUrl });
      try {
        await api?.report_ui_crash?.({
          label: this.props.label || "App",
          message: error.message || String(error),
          stack: error.stack || "",
          componentStack,
          appVersion,
        });
      } catch {
        /* ignore */
      }
    })();
  }

  componentDidUpdate(prev: ErrorBoundaryProps) {
    if (!this.state.error) return;
    const a = prev.resetKeys ?? [];
    const b = this.props.resetKeys ?? [];
    if (a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]))) {
      this.reset();
    }
  }

  reset = () => {
    // Stuck walkthrough rAF loops can re-crash on remount; clear tour on App retry.
    if (this.props.label === "App") void skipTour();
    this.setState({
      error: null,
      componentStack: "",
      detailsOpen: false,
    });
  };

  openInspector = () => {
    const api = getApi();
    void api?.open_devtools?.().catch?.(() => undefined);
  };

  copyDetails = () => {
    const { error, componentStack, appVersion, installLocation } = this.state;
    const text = [
      `UEFN Ducky crash`,
      `version: ${appVersion || "(unknown)"}`,
      `label: ${this.props.label || "App"}`,
      installLocation ? `install: ${installLocation}` : "",
      `message: ${error?.message || String(error)}`,
      error?.stack ? `stack:\n${error.stack}` : "",
      componentStack ? `componentStack:\n${componentStack}` : "",
    ]
      .filter(Boolean)
      .join("\n\n");
    void navigator.clipboard?.writeText?.(text).catch(() => undefined);
  };

  render() {
    const { error, appVersion, installLocation, downloadUrl, componentStack, detailsOpen } =
      this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    const isApp = this.props.label === "App";
    const is185 = /185|maximum update depth/i.test(error.message || "");
    const versionLine = appVersion ? `You are running v${appVersion}.` : "Detecting version…";

    return (
      <div
        className={`error-boundary-panel${isApp ? " error-boundary-panel--app" : ""}`}
        role="alert"
      >
        <div className="error-boundary-title">
          {this.props.label ? `${this.props.label} hit an error` : "Something went wrong"}
        </div>
        {isApp ? <div className="error-boundary-version">{versionLine}</div> : null}
        {isApp && installLocation ? (
          <div className="error-boundary-install muted">{installLocation}</div>
        ) : null}
        <div className="error-boundary-message">{error.message || String(error)}</div>
        {isApp ? (
          <p className="error-boundary-hint">
            {/DiscordHeaderDropdown|plugin-header|PluginSurface/i.test(componentStack)
              ? "This looks like a plugin header surface (often Discord). Newer builds isolate that crash, auto-disable the plugin, and keep the app up — download the newest Setup."
              : is185
                ? "React #185 (maximum update depth) crashed the UI. Try again often fails — open Inspector to see the component stack, or download the newest Setup and reinstall. Your chats stay on disk."
                : "If Try again keeps failing, open Inspector for the stack, or download the newest Setup and reinstall. Your chats stay on disk."}
          </p>
        ) : null}
        <div className="error-boundary-actions">
          <button type="button" className="error-boundary-retry" onClick={this.reset}>
            Try again
          </button>
          {isApp ? (
            <>
              <button
                type="button"
                className="error-boundary-retry error-boundary-retry--primary"
                onClick={() => openExternal(downloadUrl || UEFN_DUCKY_DOWNLOAD_PAGE)}
              >
                Download latest Setup
              </button>
              <button
                type="button"
                className="error-boundary-retry"
                onClick={() => openExternal(UEFN_DUCKY_DOWNLOAD_PAGE)}
              >
                Open download page
              </button>
              <button type="button" className="error-boundary-retry" onClick={this.openInspector}>
                Open Inspector
              </button>
              <button type="button" className="error-boundary-retry" onClick={this.copyDetails}>
                Copy details
              </button>
              <button
                type="button"
                className="error-boundary-retry"
                onClick={() => this.setState((s) => ({ detailsOpen: !s.detailsOpen }))}
              >
                {detailsOpen ? "Hide stack" : "Show stack"}
              </button>
            </>
          ) : null}
        </div>
        {isApp && detailsOpen ? (
          <pre className="error-boundary-stack">
            {[error.stack, componentStack].filter(Boolean).join("\n\n") || "(no stack)"}
          </pre>
        ) : null}
      </div>
    );
  }
}
