/**
 * Plugin webview tuning knobs — change these here, nowhere else.
 *
 * Deleting this folder (+ one-line hooks in EditorGroupPane / ChatView /
 * pluginHeaderActions / usePluginContributions / types/panel) removes Phase 2.
 */

/** URL path prefix served by panel_httpd → backend.uefn_plugins.webview. */
export const PLUGIN_UI_ROUTE_PREFIX = "plugin-ui";

/**
 * iframe sandbox flags.
 * - allow-scripts: run plugin JS
 * - allow-pointer-lock: games / pointer capture
 * - NO allow-same-origin: opaque origin — no host DOM, no panel cookies, no app localStorage
 * Changing this weakens isolation; do not add allow-same-origin without a security review.
 */
export const PLUGIN_UI_SANDBOX = "allow-scripts allow-pointer-lock";

/** postMessage channel tag so we ignore unrelated window messages. */
export const BRIDGE_CHANNEL = "uefn-plugin-ui";

/** How long the host waits for a bridge handler before rejecting (ms). */
export const BRIDGE_TIMEOUT_MS = 5000;

/** Header-button action prefix: `panel:<panelId>` opens that plugin's UI tab. */
export const PANEL_ACTION_PREFIX = "panel:";
