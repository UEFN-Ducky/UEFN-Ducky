import React from "react";
import ReactDOM from "react-dom/client";
import "./theme/styles/index.css";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { dismissBootSplashAfterPaint } from "./bootSplash";
import { directConfigFromLocation, isDirectMode, startDirectTransport } from "./remote/directTransport";

// Phone panel on the panel host: bring up the WebRTC transport before React
// mounts so the first PanelApi call already has somewhere to go.
if (isDirectMode()) {
  startDirectTransport(directConfigFromLocation(__PANEL_VERSION__));
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary label="App">
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);

// Keep the HTML splash up for ~3.5s (and until the app shell has painted),
// then fade it out so the logo reads clearly before the UI appears.
dismissBootSplashAfterPaint();
