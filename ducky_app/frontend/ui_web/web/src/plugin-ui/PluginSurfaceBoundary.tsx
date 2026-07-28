import { Component, type ErrorInfo, type ReactNode } from "react";
import { handlePluginFault, type PluginFaultKind } from "./pluginCrashGuard";

type Props = {
  children: ReactNode;
  /** Store plugin id (e.g. discord). Required for auto-disable / attribution. */
  pluginId: string;
  /** Where this surface lives (header-button, ui.panel, shell.boot, …). */
  surface: string;
  /** plugin = auto-disable; theme = clear skin/fx only. */
  kind?: PluginFaultKind;
  /** Header buttons: swallow UI (banner still shows). Panels: inline recovery. */
  compact?: boolean;
};

type State = {
  error: Error | null;
};

/**
 * Local ErrorBoundary for plugin-owned React surfaces.
 * On catch: never bubble to App — isolate, auto-disable (or clear theme), name the plugin.
 */
export class PluginSurfaceBoundary extends Component<Props, State> {
  state: State = { error: null };
  private handled = false;

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (this.handled) return;
    this.handled = true;
    const pluginId = String(this.props.pluginId || "")
      .trim()
      .toLowerCase();
    void handlePluginFault({
      pluginId,
      surface: this.props.surface || "ui",
      kind: this.props.kind || "plugin",
      message: error.message || String(error),
      stack: error.stack || "",
      componentStack: info.componentStack || "",
    });
  }

  componentDidUpdate(prev: Props) {
    if (!this.state.error) return;
    if (
      prev.pluginId !== this.props.pluginId ||
      prev.surface !== this.props.surface
    ) {
      this.handled = false;
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.compact) return null;

    const pluginId = this.props.pluginId || "plugin";
    const kind = this.props.kind || "plugin";
    return (
      <div className="error-boundary-panel error-boundary-panel--inline" role="alert">
        <div className="error-boundary-title">
          {kind === "theme"
            ? `Theme from plugin “${pluginId}” crashed`
            : `Plugin “${pluginId}” crashed`}
        </div>
        <div className="error-boundary-message">
          {kind === "theme"
            ? "Theme surface cleared — rest of the app kept running."
            : "Plugin was auto-disabled so the app stays up. Re-enable in Settings → Store."}
        </div>
        <div className="error-boundary-message">{error.message || String(error)}</div>
        <div className="error-boundary-actions">
          <button
            type="button"
            className="error-boundary-retry"
            onClick={() => {
              this.handled = false;
              this.setState({ error: null });
            }}
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
