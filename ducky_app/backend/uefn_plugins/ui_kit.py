"""Ducky plugin UI kit, served to every plugin panel at ``/plugin-ui/_kit/<file>``.

- ``ducky.js``: the one tag a panel needs. It links ``ducky.css`` beside it, applies
  the app's Appearance variables (``theme.get`` on load, ``appearance_theme`` after)
  and marks the page hidden on ``panel.visibility`` so kit animations pause.
- ``ducky.css``: buttons, inputs, selects, checkboxes, tabs, cards, tables, badges,
  empty / loading / error states and a visible focus ring, built only from the
  app's Appearance variables (:mod:`appearance_vars`), never a color literal.

Also :data:`PANEL_ERROR_SCRIPT`, which :mod:`webview` puts at the top of every plugin
HTML page: uncaught errors, rejected promises and ``console.error`` go to the host
over the bridge (``panel.error``), so ``ducky_plugin_errors`` can show them.

Kept as Python strings so the frozen app always carries them.
"""

from __future__ import annotations

CSS = """\
/* Ducky plugin UI kit. Colors, fonts and radii come from the app's Appearance
   variables only, so a panel follows the user's theme. Element defaults use
   :where() (no specificity): a plugin's own rules always win. */

:where(body) {
  margin: 0;
  color: var(--fg);
  background: var(--bg);
  font-family: var(--font-ui);
  font-size: var(--font-ui-size);
  line-height: 1.5;
}
:where(code, pre, kbd, samp) {
  font-family: var(--font-mono);
  font-size: var(--font-mono-size);
}
:where(a) { color: var(--accent); }
:where(a):hover { color: var(--accent-hover); }
:where(h1, h2, h3, h4) { margin: 0 0 8px; color: var(--fg); line-height: 1.25; }
:where(hr) { border: 0; border-top: 1px solid var(--border); }
.dk-muted { color: var(--muted); }
.dk-dim { color: var(--fg-dim); }
.dk-page { padding: 12px; }
.dk-stack { display: flex; flex-direction: column; gap: 8px; }
.dk-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }

/* Focus ring: every control, keyboard focus only. */
:where(button, a, input, select, textarea, summary, [tabindex]):focus-visible,
.dk-btn:focus-visible,
.dk-tab:focus-visible,
.dk-input:focus-visible,
.dk-select:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}

/* Buttons: .dk-btn plus --primary / --secondary / --ghost / --danger / --icon. */
:where(button),
.dk-btn {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 28px;
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--btn-bg);
  color: var(--fg);
  font: inherit;
  cursor: pointer;
}
:where(button):hover,
.dk-btn:hover { background: var(--btn-hover); }
:where(button):active,
.dk-btn:active { background: var(--btn-pressed); }
:where(button):disabled,
.dk-btn:disabled,
.dk-btn[aria-disabled="true"] { opacity: 0.5; cursor: not-allowed; }
.dk-btn--primary { border-color: var(--accent); background: var(--accent); color: var(--fg-inverse); }
.dk-btn--primary:hover { border-color: var(--accent-hover); background: var(--accent-hover); }
.dk-btn--primary:active { background: var(--accent); }
.dk-btn--secondary { background: var(--card); }
.dk-btn--secondary:hover { background: var(--card-hover); }
.dk-btn--ghost { padding: 5px 13px; border: 0; background: none; color: var(--fg-dim); }
.dk-btn--ghost:hover { background: var(--tab-hover); color: var(--fg); }
.dk-btn--danger { border-color: var(--red); background: var(--red-dim); color: var(--red); }
.dk-btn--danger:hover { background: var(--red); color: var(--fg-inverse); }
.dk-btn--icon { width: 28px; min-width: 28px; padding: 0; }
.dk-btn--icon svg { width: 16px; height: 16px; }

/* Inputs, textareas and selects. */
:where(input:not([type="checkbox"], [type="radio"], [type="range"], [type="color"], [type="file"]), textarea, select),
.dk-input,
.dk-select {
  box-sizing: border-box;
  min-height: 28px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--input-bg);
  color: var(--fg);
  font: inherit;
}
:where(input, textarea, select):hover,
.dk-input:hover,
.dk-select:hover { border-color: var(--border-light); }
:where(input, textarea, select):focus,
.dk-input:focus,
.dk-select:focus { border-color: var(--border-focus); }
:where(input, textarea)::placeholder { color: var(--muted); }
:where(input, textarea, select):disabled { opacity: 0.5; cursor: not-allowed; }
:where(select) option,
.dk-select option { background: var(--dropdown-bg); color: var(--fg); }
.dk-input[aria-invalid="true"] { border-color: var(--red); }
.dk-field { display: flex; flex-direction: column; gap: 4px; }
.dk-field__label { color: var(--fg-dim); font-weight: 600; }
.dk-field__hint { color: var(--muted); }

/* Checkboxes and radios: <label class="dk-check"><input type="checkbox"> Label</label>. */
:where(input[type="checkbox"], input[type="radio"]) {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--accent);
}
.dk-check { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }

/* Tabs: .dk-tabs > button.dk-tab[aria-selected="true"]. */
.dk-tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); }
.dk-tab {
  min-height: 0;
  padding: 6px 12px;
  border: 0;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  background: none;
  color: var(--fg-dim);
  font: inherit;
  cursor: pointer;
}
.dk-tab:hover { background: var(--tab-hover); color: var(--fg); }
.dk-tab[aria-selected="true"],
.dk-tab.is-active { background: var(--tab-active); color: var(--fg); box-shadow: inset 0 -2px 0 var(--accent); }

/* Cards. */
.dk-card {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card);
  box-shadow: var(--shadow-sm);
}
.dk-card--interactive { cursor: pointer; }
.dk-card--interactive:hover { background: var(--card-hover); border-color: var(--border-light); }
.dk-card__title { margin: 0 0 6px; font-weight: 600; }

/* Tables. */
.dk-table { width: 100%; border-collapse: collapse; }
.dk-table th,
.dk-table td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
.dk-table th { background: var(--panel-header); color: var(--fg-dim); font-weight: 600; }
.dk-table tbody tr:hover { background: var(--bg-hover); }

/* Badges: .dk-badge plus --accent / --success / --warn / --danger / --info. */
.dk-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-elevated);
  color: var(--fg-dim);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.dk-badge--accent { border-color: var(--accent); color: var(--accent); }
.dk-badge--success { border-color: var(--green); background: var(--green-dim); color: var(--green); }
.dk-badge--warn { border-color: var(--amber); background: var(--amber-dim); color: var(--amber); }
.dk-badge--danger { border-color: var(--red); background: var(--red-dim); color: var(--red); }
.dk-badge--info { border-color: var(--blue); background: var(--blue-dim); color: var(--blue); }

/* Empty, loading and error states. */
.dk-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 16px;
  color: var(--muted);
  text-align: center;
}
.dk-empty__title { color: var(--fg); font-weight: 600; }
.dk-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--muted);
}
.dk-loading::before {
  content: "";
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: dk-spin 0.8s linear infinite;
}
@keyframes dk-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .dk-loading::before { animation-duration: 2.4s; }
}
.dk-error {
  padding: 10px 12px;
  border: 1px solid var(--red);
  border-radius: var(--radius-sm);
  background: var(--red-dim);
  color: var(--red);
}
.dk-error__title { font-weight: 600; }

/* The panel's tab is hidden (panel.visibility): kit animations pause. */
:where(html[data-dk-hidden]) *,
:where(html[data-dk-hidden]) *::before,
:where(html[data-dk-hidden]) *::after { animation-play-state: paused; }
"""

JS = """\
/* Ducky plugin UI kit: <script src="../../_kit/ducky.js"></script> in a panel page.
   Links ducky.css, applies the app's Appearance variables (now and on every theme
   change) and sets html[data-dk-hidden] while the panel's tab is hidden. */
(function () {
  "use strict";
  var CHANNEL = "uefn-plugin-ui";
  var root = document.documentElement;
  var me = document.currentScript;
  if (me && me.src && !document.querySelector("link[data-ducky-kit]")) {
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = me.src.replace(/ducky\\.js(\\?.*)?$/, "ducky.css");
    link.setAttribute("data-ducky-kit", "");
    (document.head || root).appendChild(link);
  }
  function theme(vars) {
    if (!vars || typeof vars !== "object") return;
    Object.keys(vars).forEach(function (key) {
      var value = vars[key];
      if (typeof value === "string") root.style.setProperty("--" + key.replace(/^--/, ""), value);
    });
  }
  window.addEventListener("message", function (ev) {
    var data = ev.data;
    if (!data || data.channel !== CHANNEL) return;
    if (data.id === "ducky-kit-theme" && data.ok && data.result) theme(data.result.vars);
    var event = data.event;
    if (!event) return;
    if (event.type === "appearance_theme") theme(event.vars);
    if (event.type === "panel.visibility") {
      if (event.visible) root.removeAttribute("data-dk-hidden");
      else root.setAttribute("data-dk-hidden", "");
    }
  });
  parent.postMessage({ channel: CHANNEL, id: "ducky-kit-theme", method: "theme.get", params: {} }, "*");
})();
"""

# Put first in every plugin HTML page (webview.py). At most 20 reports per page load.
PANEL_ERROR_SCRIPT = (
    "<script>(function(){var C='uefn-plugin-ui',n=0;"
    "function send(kind,msg,stack){if(n>=20)return;n++;try{parent.postMessage({channel:C,"
    "id:'ducky-panel-error-'+n,method:'panel.error',params:{kind:kind,"
    "message:String(msg==null?'':msg).slice(0,1000),stack:String(stack==null?'':stack).slice(0,4000)}},'*')}"
    "catch(e){}}"
    "window.addEventListener('error',function(e){if(e&&e.message)send('error',e.message,e.error&&e.error.stack)},true);"
    "window.addEventListener('unhandledrejection',function(e){var r=e&&e.reason;"
    "send('rejection',r&&r.message||r,r&&r.stack)});"
    "var ce=console.error;console.error=function(){try{send('console',Array.prototype.map.call(arguments,"
    "function(a){if(a&&a.stack)return a.stack;if(typeof a==='string')return a;try{return JSON.stringify(a)}"
    "catch(e){return String(a)}}).join(' '))}catch(e){}return ce.apply(console,arguments)};})();</script>"
)

FILES: dict[str, tuple[str, str]] = {
    "ducky.css": (CSS, "text/css; charset=utf-8"),
    "ducky.js": (JS, "text/javascript; charset=utf-8"),
}
