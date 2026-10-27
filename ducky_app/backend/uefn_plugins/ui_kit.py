"""Ducky plugin UI kit, served to every plugin panel at ``/plugin-ui/_kit/<file>``.

- ``ducky.js``: the one tag a panel needs. It links ``ducky.css`` beside it, applies
  the app's Appearance variables (``theme.get`` on load, ``appearance_theme`` after)
  and marks the page hidden on ``panel.visibility`` so kit animations pause.
- ``ducky.css``: the page layout (floating side panels, a frameless main area), nav
  rows, buttons, inputs, selects, checkboxes, choice rows, pill tabs, cards, tables,
  badges, empty / loading / error states and a visible focus ring, built only from
  the app's Appearance variables (:mod:`appearance_vars`), never a color literal.

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

/* Tabs: .dk-tabs > button.dk-tab[aria-selected="true"]. Rounded pills on the page
   background, like the app's editor tabs; no strip, no underline. */
.dk-tabs { display: flex; flex-wrap: wrap; gap: 4px; }
.dk-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 0;
  padding: 5px 12px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--fg-dim);
  font: inherit;
  cursor: pointer;
}
.dk-tab:hover { background: var(--tab-hover); color: var(--fg); }
.dk-tab[aria-selected="true"],
.dk-tab.is-active { background: var(--tab-active); color: var(--fg); }

/* Page layout, like the app: side panels are floating cards, the main area is the page
   background itself (no frame). <div class="dk-layout"><aside class="dk-sidebar">…</aside>
   <main class="dk-main">…</main></div>; .dk-sidebar--right for a panel on the right. */
.dk-layout {
  box-sizing: border-box;
  display: flex;
  gap: 6px;
  height: 100vh;
  padding: 6px;
}
.dk-sidebar {
  box-sizing: border-box;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 220px;
  min-height: 0;
  padding: 8px 4px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--panel-header);
}
.dk-sidebar--right { order: 2; }
.dk-sidebar__title {
  padding: 4px 8px 6px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.dk-main { flex: 1 1 auto; min-width: 0; min-height: 0; overflow-y: auto; }

/* Nav rows in a side panel: icon + label, rounded and inset, the open one lit.
   <button class="dk-nav-item" aria-current="page"><svg…/> Label</button>. */
.dk-nav-item {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  width: 100%;
  min-height: 30px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--fg-dim);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.dk-nav-item svg { flex: none; width: 15px; height: 15px; }
.dk-nav-item:hover { background: var(--tab-hover); color: var(--fg); }
.dk-nav-item[aria-current="page"],
.dk-nav-item.is-active { background: var(--tab-active); color: var(--fg); }

/* Choice rows instead of radio circles: an icon tile, a title and hint, and a check on
   the picked one. <button class="dk-choice" role="radio" aria-checked="true">
   <span class="dk-choice__icon">…</span><span class="dk-choice__text"><b>Title</b>
   <span class="dk-choice__hint">Hint</span></span></button>. */
.dk-choice {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  width: 100%;
  min-height: 0;
  padding: 7px 10px 7px 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.dk-choice:hover { background: var(--tab-hover); }
.dk-choice[aria-checked="true"],
.dk-choice.is-selected {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
.dk-choice__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  background: var(--tab-active);
  color: var(--fg-dim);
}
.dk-choice[aria-checked="true"] .dk-choice__icon,
.dk-choice.is-selected .dk-choice__icon {
  background: color-mix(in srgb, var(--accent) 24%, transparent);
  color: var(--accent);
}
.dk-choice__text { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.dk-choice__hint { color: var(--muted); font-size: 11px; }
.dk-choice[aria-checked="true"]::after,
.dk-choice.is-selected::after {
  content: "\\2713";
  flex: none;
  color: var(--accent);
  font-weight: 700;
}

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

# Put first in every plugin HTML page (webview.py), kit or not: the app's own scrollbars (global.css),
# thin and hidden until the scrolling area is hovered, so no plugin writes them. Zero specificity
# (:where) and before the plugin's CSS: a plugin's own scrollbar-width / -color wins. Those
# properties switch off ::-webkit-scrollbar styling in Chromium, so a page that styles its
# scrollbars that way gets html[data-dk-own-scrollbars] and these rules step aside (a rule that
# only hides one, display: none, doesn't count).
PANEL_SCROLLBAR_STYLE = (
    "<style>"
    ":where(html:not([data-dk-own-scrollbars])),:where(html:not([data-dk-own-scrollbars]) *)"
    "{scrollbar-width:thin;scrollbar-color:transparent transparent}"
    ":where(html:not([data-dk-own-scrollbars]):hover),:where(html:not([data-dk-own-scrollbars]) *:hover)"
    "{scrollbar-color:var(--border-light,rgba(128,128,128,.45)) transparent}"
    "</style>"
    "<script>(function(){var mine=document.currentScript&&document.currentScript.previousElementSibling;"
    "function own(rules){for(var i=0;i<rules.length;i++){var r=rules[i];"
    "if(r.selectorText&&r.selectorText.indexOf('::-webkit-scrollbar')>=0&&r.style.display!=='none')return true;"
    "if(r.cssRules&&own(r.cssRules))return true}return false}"
    "function look(){var s=document.styleSheets;for(var i=0;i<s.length;i++){if(s[i].ownerNode===mine)continue;"
    "try{if(own(s[i].cssRules)){document.documentElement.setAttribute('data-dk-own-scrollbars','');return}}catch(e){}}}"
    "document.addEventListener('DOMContentLoaded',look);window.addEventListener('load',look)})();</script>"
)

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

# Live editing in every panel, with no plugin code.
#
# While the host says the plugin is live (bridge event ``live.state``):
# - the pointer is reported about 20 times a second as the element under it (an anchor:
#   ``data-live-id``, else an id, else a short tag path from the nearest id), the point as a
#   fraction of that element, and the screen it is on; a click on something that isn't a
#   control reports a selection (an outline for 10 s);
# - the group shares one screen, through its leader (bridge event ``live.follow``: the
#   leader's device while following it, and ``leading`` on the leader's own page). The
#   leader's screen (``live.view``: a view id, the plugin's ``data-live-view`` as ``at``, and
#   scroll positions; again every 5 s, so a page that joins or reloads catches up) and its
#   tab clicks (``live.nav``: ``role="tab"``, ``a[href^="#"]``, ``data-live-nav``) go to the
#   group, and every page following it goes to the same screen (the window event
#   ``ducky-live-goto`` with ``detail.view`` = the leader's ``at``, which the plugin opens),
#   scrolls and clicks with it. A member's own click, scroll or move to another screen goes
#   to the leader instead (``ask``), which does it and tells everyone; each page does each
#   click once (``o``: the page that did it first never does it again). A component's own
#   state (a player playing, a picked clip) the plugin shares with ``live.share``.
#   Teammates' pointers are drawn with their name in a click-through layer and fade after
#   5 s; one on another screen, or whose element isn't on this one, doesn't show.
#
# On a team copy (bridge event ``sync.state``), live or not:
# - pressing a control holds it (``live.hold``): a text field, list or editable while it has
#   focus, any other control or anything dragged until 1.5 s after the button comes up. Every
#   teammate on the same screen sees it greyed out (``inert``, a box with the holder's name),
#   so two people never change the same thing at once: the group at once (``ducky.hold``),
#   everyone else through presence (``live.holds``). Holds are renewed every 10 s and dropped
#   after 30 s without one. Two presses: the earlier one keeps it, the other lets go and loses
#   focus. ``data-live-lock`` makes anything holdable, ``data-live-nolock`` opts a control
#   out; links and tabs never hold. ``data-live-view`` marks what a screen shows (a project
#   id, say) so holds and pointers never cross screens;
# - a picture or video that fails to load shows a placeholder the size of the picture,
#   saying "Getting picture…" while teammates' changes come in, and loads again after each
#   sync (``plugin_scope_changed``).
LIVE_PRESENCE_SCRIPT = r"""<script>(function () {
  var C = "uefn-plugin-ui", on = false, n = 0, last = 0, cur = {}, sel = {}, L = null;
  var mine = {}, theirs = {}, far = [], boxes = {}, before = new WeakMap(), down = null, shared = false, incoming = 0, missing = [];
  var following = "", replaying = false, scrolled = [], sentView = "", viewAt = 0, holdsAsk = "";
  var lead = null, wentTo = "", wentFrom = "", wentAt = 0, scrolledTo = "", peers = {}, reached = {};
  var leading = false, me = Math.random().toString(36).slice(2, 10), seq = 0, leadAt = "", askedAt = "";
  var appliedAt = 0, selfScrollAt = 0, older = {}, heard = false;
  var HOLD_AFTER_MS = 1500, HOLD_RENEW_MS = 10000, HOLD_STALE_MS = 30000, VIEW_GAP_MS = 250, VIEW_EVERY_MS = 5000;
  var ECHO_MS = 400, OWN_SCROLL_MS = 1000;
  var CONTROL = 'input,textarea,select,button,[contenteditable=""],[contenteditable="true"],[role="button"],[role="slider"],' +
    '[role="checkbox"],[role="switch"],[role="textbox"],[role="spinbutton"],[role="combobox"],[role="option"],' +
    '[role="menuitem"],[draggable="true"],[data-live-lock]';
  var TEXT = 'textarea,select,[contenteditable=""],[contenteditable="true"],[role="textbox"],[role="spinbutton"],[role="combobox"]';
  var NAV = '[role="tab"],a[href^="#"],[data-live-nav]';
  var NOT_TEXT = /^(button|submit|reset|checkbox|radio|range|color|file|image)$/;
  var DOC = "@doc";
  function req(m, p, id) { try { parent.postMessage({ channel: C, id: id || "ducky-live-" + (++n), method: m, params: p }, "*"); } catch (e) {} }
  function q(s) { return String(s).replace(/["\\]/g, "\\$&"); }
  function anchor(el) {
    if (el === document.scrollingElement || el === document.documentElement) return DOC;
    var parts = [], e = el;
    for (var d = 0; e && e.nodeType === 1 && e !== document.body && d < 8; d++, e = e.parentElement) {
      var lid = e.getAttribute("data-live-id");
      if (lid) { parts.unshift('[data-live-id="' + q(lid) + '"]'); return parts.join(">"); }
      if (e.id) { parts.unshift('[id="' + q(e.id) + '"]'); return parts.join(">"); }
      var i = 1, s = e;
      while ((s = s.previousElementSibling)) if (s.tagName === e.tagName) i++;
      parts.unshift(e.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
    }
    parts.unshift("body");
    return parts.join(">");
  }
  function find(a) {
    if (a === DOC) return document.scrollingElement || document.documentElement;
    try { return a ? document.querySelector(a) : null; } catch (e) { return null; }
  }
  function marks() {  // the screen as the plugin names it (data-live-view)
    var m = document.querySelectorAll("[data-live-view]"), parts = [];
    for (var i = 0; i < m.length && i < 8; i++) parts.push(m[i].getAttribute("data-live-view"));
    return parts;
  }
  // The screen this page shows: the plugin's data-live-view marks, else its title, address
  // and selected tabs. A hold or pointer from another screen never shows on this one.
  function view() {
    var parts = marks(), i;
    if (!parts.length) {
      parts.push(document.title, location.hash);
      var picked = document.querySelectorAll('[aria-selected="true"],[aria-current]:not([aria-current="false"])');
      for (i = 0; i < picked.length && i < 8; i++) parts.push((picked[i].textContent || "").trim().slice(0, 40));
    }
    var s = parts.join("|"), h = 0;
    for (i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return "v" + h.toString(36);
  }
  function here(v) { return !v || v === view(); }
  function layer() {
    if (!L) {
      L = document.createElement("div");
      L.setAttribute("data-ducky-live", "");
      L.style.cssText = "position:fixed;left:0;top:0;width:0;height:0;pointer-events:none;z-index:2147483647";
      document.documentElement.appendChild(L);
    }
    return L;
  }
  function ours(el) { return L && el && (el === L || L.contains(el)); }
  function tone(k) { var h = 0; for (var i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0; return "hsl(" + (h % 360) + ",80%,58%)"; }
  function key(f) { return String(f.device || f.user || ""); }
  function tag(col, text, lock) {
    var lab = document.createElement("span");
    lab.style.cssText = "position:absolute;left:-2px;top:-20px;display:inline-flex;align-items:center;gap:4px;padding:1px 6px;" +
      "border-radius:4px;font:600 11px system-ui,sans-serif;color:#000;white-space:nowrap";
    lab.style.background = col;
    if (lock) lab.innerHTML = '<svg width="10" height="11" viewBox="0 0 10 11"><rect x="1" y="5" width="8" height="6" rx="1" fill="#000"/>' +
      '<path d="M3 5V3.5a2 2 0 0 1 4 0V5" stroke="#000" stroke-width="1.4" fill="none"/></svg>';
    lab.appendChild(document.createTextNode(String(text || "Teammate").slice(0, 40)));
    return lab;
  }
  function box(col, text, lock) {
    var el = document.createElement("div");
    el.style.cssText = "position:absolute;left:0;top:0;border:2px solid;border-radius:4px;box-sizing:border-box";
    el.style.borderColor = col;
    if (lock) { el.style.borderStyle = "dashed"; el.style.background = "rgba(128,128,128,.28)"; }
    el.appendChild(tag(col, text, lock));
    layer().appendChild(el);
    return el;
  }
  function over(el, t) {
    if (!t) { el.style.display = "none"; return; }
    var r = t.getBoundingClientRect();
    el.style.display = "block";
    el.style.transform = "translate(" + r.left + "px," + r.top + "px)";
    el.style.width = r.width + "px";
    el.style.height = r.height + "px";
  }
  function pointer(f, d) {
    var k = key(f); if (!k) return;
    var c = cur[k];
    if (!c) {
      var el = document.createElement("div"), col = tone(k);
      el.style.cssText = "position:absolute;left:0;top:0;transition:transform 60ms linear,opacity .4s";
      el.innerHTML = '<svg width="16" height="20" viewBox="0 0 16 20"><path d="M1 1l13 10-6 1-3 6z" stroke="#000" stroke-width="1"/></svg><span></span>';
      el.firstChild.firstChild.setAttribute("fill", col);
      var lab = el.lastChild;
      lab.style.cssText = "position:absolute;left:14px;top:14px;padding:1px 6px;border-radius:4px;font:600 11px system-ui,sans-serif;color:#000;white-space:nowrap";
      lab.style.background = col;
      lab.textContent = String(f.name || "Teammate").slice(0, 40);
      layer().appendChild(el);
      c = cur[k] = { el: el, at: 0 };
    }
    var t = here(d.v) ? find(d.anchor) : null;
    if (!t) { c.el.style.display = "none"; return; }
    var r = t.getBoundingClientRect();
    c.el.style.display = "block";
    c.el.style.opacity = "1";
    c.el.style.transform = "translate(" + (r.left + (+d.fx || 0) * r.width) + "px," + (r.top + (+d.fy || 0) * r.height) + "px)";
    c.at = Date.now();
  }
  function select(f, d) {
    var k = key(f); if (!k) return;
    var s = sel[k] || (sel[k] = { el: box(tone(k), f.name), at: 0, a: "" });
    s.a = String(d.anchor || ""); s.at = Date.now(); over(s.el, find(s.a));
  }

  // -- holds: what this PC presses, and what teammates press here (live at once, else through presence)
  function control(el) {
    if (!el || el.nodeType !== 1 || ours(el) || !el.closest || el.closest("[data-live-nolock],a[href],[role=tab]")) return null;
    return el.closest(CONTROL);
  }
  function isText(el) { return el.matches(TEXT) || (el.tagName === "INPUT" && !NOT_TEXT.test(el.type)); }
  function holding() { return on || shared; }
  function others() {  // anchor -> the hold that greys it here, pressed first wins
    var out = {}, v = view(), k, a, h, i;
    function take(anc, row) { if ((!row.v || row.v === v) && (!out[anc] || row.t < out[anc].t)) out[anc] = row; }
    for (k in theirs) for (a in theirs[k]) take(a, theirs[k][a]);
    for (i = 0; i < far.length; i++) {
      h = far[i];
      if (h && typeof h.anchor === "string" && !(theirs[h.device] && theirs[h.device][h.anchor]))
        take(h.anchor, { t: +h.t || 0, v: String(h.v || ""), name: String(h.name || "Teammate"), col: tone(String(h.device || h.name)) });
    }
    return out;
  }
  function hold(el) {
    var a = anchor(el), held = others()[a];
    if (held) return "";
    var h = mine[a];
    if (!h) { h = mine[a] = { t: Date.now(), sent: Date.now(), v: view() }; req("live.hold", { anchor: a, on: true, t: h.t, v: h.v }); }
    h.end = 0;
    return a;
  }
  function letGo(a) { if (mine[a]) mine[a].end = Date.now() + HOLD_AFTER_MS; }
  function yieldTo(a, t) {  // a teammate pressed the same control first: this PC lets go of it
    var m = mine[a];
    if (!m || m.t < t) return;
    delete mine[a];
    req("live.hold", { anchor: a, on: false });
    var el = find(a), act = document.activeElement;
    if (el && act && el.contains(act) && act.blur) act.blur();
  }
  function theirHold(f, d) {
    var k = key(f), a = d && typeof d.anchor === "string" ? d.anchor : "";
    if (!k || !a) return;
    var hs = theirs[k] || (theirs[k] = {});
    if (d.on === false) { delete hs[a]; paint(); return; }
    var t = +d.t || 0, m = mine[a];
    if (m && m.t < t) return;  // pressed here first: they let go of it
    yieldTo(a, t);
    hs[a] = { at: Date.now(), t: t, v: String(d.v || ""), name: String(f.name || "Teammate"), col: tone(k) };
    paint();
  }
  function farHolds(list) {
    far = Array.isArray(list) ? list : [];
    for (var i = 0; i < far.length; i++) if (far[i] && here(far[i].v)) yieldTo(far[i].anchor, +far[i].t || 0);
    paint();
  }
  function free(el) {
    var b = before.get(el); if (!b) return;
    before.delete(el);
    el.inert = b.inert;
    if (b.aria === null) el.removeAttribute("aria-disabled"); else el.setAttribute("aria-disabled", b.aria);
    el.removeAttribute("data-ducky-held");
  }
  function paint() {
    var want = others();
    for (var b in boxes) if (!want[b]) { free(boxes[b].t); boxes[b].el.remove(); delete boxes[b]; }
    for (var a in want) {
      var h = want[a], x = boxes[a] || (boxes[a] = { el: box(h.col, h.name, true), t: null }), t = find(a);
      if (x.t && x.t !== t) free(x.t);
      if (t && !before.has(t)) {
        before.set(t, { inert: !!t.inert, aria: t.getAttribute("aria-disabled") });
        t.inert = true;
        t.setAttribute("aria-disabled", "true");
        t.setAttribute("data-ducky-held", h.name);
      }
      x.t = t;
      over(x.el, t);
    }
  }
  function clear() {
    for (var k in cur) cur[k].el.remove();
    for (var j in sel) sel[j].el.remove();
    cur = {}; sel = {}; theirs = {}; peers = {}; down = null;
    if (!holding()) mine = {};
    paint();
  }

  // -- one screen for the group, through its leader. What the leader does on its screen (a
  // tab click, a scroll, opening another screen) every page following it does too; what a
  // member does goes to the leader (``ask``), which does it and tells everyone, so every page
  // sees the same order. Each click reaches each page once: ``o`` names the page that did it
  // first, which never does it again. The leader's screen goes out every 5 s too, so a page
  // that drifted or joined late catches up.
  function origin() { return me + ":" + (++seq); }
  function own(o) { return typeof o === "string" && o.indexOf(me + ":") === 0; }
  function asking() { return on && !leading && !!following; }  // a member on the group's screen
  function navOf(el) { return el && el.nodeType === 1 && !ours(el) && el.closest ? el.closest(NAV) : null; }
  function scrollRows() {
    var rows = [], seen = [document.scrollingElement || document.documentElement].concat(scrolled);
    for (var i = 0; i < seen.length && rows.length < 8; i++) {
      var el = seen[i];
      if (el && (el === seen[0] || el.isConnected)) rows.push({ anchor: anchor(el), top: el.scrollTop || 0, left: el.scrollLeft || 0 });
    }
    return rows;
  }
  function sendView(force) {
    if (!on || replaying) return;
    var t = Date.now(), v = view();
    if (!force && v === sentView && t - viewAt < VIEW_GAP_MS) return;
    var d = { v: v, at: marks().join("|").slice(0, 400), scroll: scrollRows() };
    if (asking()) {  // a member: only its own moves, and only to the leader
      if (!heard) return;  // just joined: the group's screen comes first, never this one's
      var moved = d.at && d.at !== leadAt && d.at !== wentTo && d.at !== askedAt;
      if (!moved && (force || t - appliedAt < ECHO_MS)) return;  // a heartbeat, or the leader's scroll landing here
      askedAt = d.at; d.ask = 1; d.to = following;
    }
    sentView = v; viewAt = t;
    req("live.view", d);
  }
  // The leader's screen: this page goes there when the leader moved (the plugin opens it),
  // again every 5 s while this page is still where the last try left it (not there yet); and
  // scrolls with it once it shows the same screen. A member's own move went to the leader
  // instead (``sendView``), so the leader moving there brings everyone.
  function follow(d) {
    var at = typeof d.at === "string" ? d.at : "", mine = marks().join("|"), t = Date.now();
    if (at && at !== mine && (at !== wentTo || (mine === wentFrom && t - wentAt >= VIEW_EVERY_MS))) {
      wentTo = at; wentFrom = mine; wentAt = t;
      window.dispatchEvent(new CustomEvent("ducky-live-goto", { detail: { view: at } }));
    }
    leadAt = at;
    heard = true;
    lead = d;
    catchUp();
  }
  function catchUp() {
    if (!lead || !here(lead.v)) return;
    var rows = lead.scroll || [], s = JSON.stringify(rows);
    lead = null;
    if (s === scrolledTo || Date.now() - selfScrollAt < OWN_SCROLL_MS) return;  // scrolling here right now wins
    scrolledTo = s;
    appliedAt = Date.now();
    replaying = true;
    try {
      rows.forEach(function (r) {
        var el = find(r.anchor);
        if (el) { el.scrollTop = +r.top || 0; el.scrollLeft = +r.left || 0; }
      });
    } finally { setTimeout(function () { replaying = false; }, 50); }
  }
  function navigate(d) {
    var el = navOf(find(d.anchor));
    if (!el) return false;
    replaying = true;
    try { el.click(); } finally { replaying = false; }
    appliedAt = Date.now();
    return true;
  }
  // A groupmate on an older app (``older``) asks nobody and its screen follows only the room's
  // PC: every page on the group's screen does its tab clicks straight away (nobody relays them,
  // so it never hears its own click back). Its scroll and screen stay its own.
  function theirNav(f, d) {
    if (older[key(f)] && !d.ask) {
      if ((leading || following) && here(d.v)) navigate(d);
      return;
    }
    if (leading && d.ask) {  // a member's click: the leader does it, then everyone (theirs once)
      if (here(d.v) && navigate(d)) req("live.nav", { anchor: d.anchor, v: d.v, o: String(d.o || "") });
      return;
    }
    if (following && key(f) === following && !d.ask && !own(d.o) && here(d.v)) navigate(d);
  }
  function theirView(f, d) {
    if (leading && d.ask) {  // a member opened another screen or scrolled: the leader goes too, and everyone with it
      var at = typeof d.at === "string" ? d.at : "", mine = marks().join("|");
      if (at && at !== mine) {
        wentTo = at; wentFrom = mine; wentAt = Date.now();
        window.dispatchEvent(new CustomEvent("ducky-live-goto", { detail: { view: at } }));
      }
      if (Date.now() - selfScrollAt >= OWN_SCROLL_MS) { lead = { v: d.v, scroll: d.scroll }; catchUp(); }
      setTimeout(function () { sendView(true); }, 100);
      return;
    }
    if (following && key(f) === following && !d.ask) follow(d);
  }

  // -- pictures that aren't on this PC yet
  function placeholder() {
    var text = incoming ? "Getting picture\u2026" : "Not on this PC yet";
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
      '<rect width="320" height="180" fill="#80808033"/><g fill="none" stroke="#8a8a8a" stroke-width="2">' +
      '<rect x="138" y="54" width="44" height="34" rx="4"/><path d="M142 84l10-10 8 8 6-6 12 12"/></g>' +
      '<text x="160" y="116" font-family="system-ui,sans-serif" font-size="13" fill="#8a8a8a" text-anchor="middle">' + text + "</text></svg>");
  }
  function ph(el) { return String(el.getAttribute("src") || "").indexOf("data:image/svg+xml") === 0; }
  function stale(el) {  // the plugin loaded something else in it since: not ours any more
    return !el.isConnected || (el.tagName === "IMG" && !ph(el) && el.getAttribute("src") !== el.getAttribute("data-ducky-src"));
  }
  function showMissing(el) {
    if (!shared || stale(el)) return;
    if (el.tagName === "VIDEO") el.poster = placeholder(); else el.setAttribute("src", placeholder());
  }

  setInterval(function () {
    var t = Date.now();
    for (var k in cur) if (t - cur[k].at > 5000) cur[k].el.style.opacity = "0";
    for (var j in sel) { if (t - sel[j].at > 10000) { sel[j].el.remove(); delete sel[j]; } else over(sel[j].el, find(sel[j].a)); }
    for (var a in mine) {
      var h = mine[a];
      if (h.end && t >= h.end) { delete mine[a]; if (holding()) req("live.hold", { anchor: a, on: false }); }
      else if (t - h.sent >= HOLD_RENEW_MS) { h.sent = t; req("live.hold", { anchor: a, on: true, t: h.t, v: h.v }); }
    }
    for (var p in theirs) for (var b in theirs[p]) if (t - theirs[p][b].at > HOLD_STALE_MS) delete theirs[p][b];
    if (on && (view() !== sentView || t - viewAt >= VIEW_EVERY_MS)) sendView(true);
    if (following) catchUp();
    paint();
  }, 1000);
  function replace() { for (var j in sel) over(sel[j].el, find(sel[j].a)); for (var b in boxes) over(boxes[b].el, boxes[b].t); }
  window.addEventListener("scroll", function (e) {
    replace();
    var el = e.target === document ? document.scrollingElement : e.target;
    if (el && el.nodeType === 1 && el !== document.scrollingElement && scrolled.indexOf(el) < 0) {
      scrolled.unshift(el); scrolled.length = Math.min(scrolled.length, 7);
    }
    if (!replaying && Date.now() - appliedAt >= ECHO_MS) selfScrollAt = Date.now();
    sendView(false);
  }, true);
  window.addEventListener("resize", replace);

  document.addEventListener("pointermove", function (e) {
    if (down && !down.a && holding() && e.buttons && Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) > 4) down.a = hold(down.el);
    if (!on || document.hidden) return;
    var t = Date.now(); if (t - last < 50) return;
    var el = e.target; if (!el || el.nodeType !== 1 || ours(el)) return;
    var r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    last = t;
    req("live.pointer", { anchor: anchor(el), fx: Math.round((e.clientX - r.left) / r.width * 1e4) / 1e4,
      fy: Math.round((e.clientY - r.top) / r.height * 1e4) / 1e4, v: view() });
  }, true);
  document.addEventListener("pointerdown", function (e) {
    var el = e.target; if (!el || el.nodeType !== 1 || ours(el)) return;
    var c = control(el);
    if (c) { down = isText(c) || !holding() ? null : { x: e.clientX, y: e.clientY, el: c, a: hold(c) }; return; }  // a text control: its focus holds it
    down = { x: e.clientX, y: e.clientY, el: el, a: "" };
    if (on) req("live.select", { anchor: anchor(el) });
  }, true);
  function up() { if (down && down.a) letGo(down.a); down = null; }
  document.addEventListener("pointerup", up, true);
  document.addEventListener("pointercancel", up, true);
  document.addEventListener("click", function (e) {
    var el = navOf(e.target);
    if (!on || !el || replaying) return;
    var d = { anchor: anchor(el), v: view(), o: origin() };
    if (asking()) { d.ask = 1; d.to = following; }
    req("live.nav", d);
  }, true);
  document.addEventListener("focusin", function (e) {
    if (!holding()) return;
    var c = control(e.target);
    if (c && isText(c)) hold(c);
  }, true);
  document.addEventListener("focusout", function (e) {
    var c = control(e.target);
    if (c && isText(c)) letGo(anchor(c));
  }, true);
  document.addEventListener("error", function (e) {
    var el = e.target;
    if (!el || (el.tagName !== "IMG" && el.tagName !== "VIDEO") || ours(el)) return;
    if (el.tagName === "IMG" && ph(el)) return;  // the placeholder itself
    if (missing.indexOf(el) < 0) missing.push(el);
    if (el.tagName === "IMG") el.setAttribute("data-ducky-src", el.getAttribute("src") || "");
    showMissing(el);
  }, true);

  window.addEventListener("message", function (ev) {
    if (ev.source && ev.source !== parent) return;
    var d = ev.data; if (!d || d.channel !== C) return;
    if (holdsAsk && d.id === holdsAsk) { holdsAsk = ""; if (d.ok && d.result) farHolds(d.result.holds); return; }
    if (!d.event) return;
    var e = d.event, from = e.from || {};
    if (e.type === "live.state") { on = !!e.on; if (!on) clear(); else sendView(true); }
    else if (e.type === "live.message" && on && e.topic === "ducky.pointer") pointer(from, e.data || {});
    else if (e.type === "live.message" && on && e.topic === "ducky.select") select(from, e.data || {});
    else if (e.type === "live.message" && on && e.topic === "ducky.hold") theirHold(from, e.data || {});
    else if (e.type === "live.message" && on && e.topic === "ducky.view") theirView(from, e.data || {});
    else if (e.type === "live.message" && on && e.topic === "ducky.nav") theirNav(from, e.data || {});
    else if (e.type === "live.follow") {
      if (String(e.device || "") !== following || !!e.leading !== leading) {  // not when only ``older`` moved
        following = String(e.device || ""); leading = !!e.leading;
        lead = null; wentTo = wentFrom = scrolledTo = leadAt = askedAt = ""; heard = false;
      }
      older = {};
      (Array.isArray(e.older) ? e.older : []).forEach(function (o) { older[String(o)] = 1; });
    }
    else if (e.type === "live.holds") farHolds(e.holds);
    else if (e.type === "live.peers") {
      var keep = {}, linked = {}, fresh = false;
      (e.peers || []).forEach(function (p) {
        var pk = key(p); keep[pk] = 1;
        // Reachable ("none": in the group, no link yet; a view sent then reaches nobody).
        if (pk && p.path !== "none") { linked[pk] = 1; if (!reached[pk]) fresh = true; }
      });
      peers = keep; reached = linked;
      if (fresh && on) sendView(true);  // someone new: they get this screen now, not in up to 5 s
      for (var k in cur) if (!keep[k]) { cur[k].el.remove(); delete cur[k]; }
      for (var j in sel) if (!keep[j]) { sel[j].el.remove(); delete sel[j]; }
      for (var h in theirs) if (!keep[h]) delete theirs[h];
      paint();
    } else if (e.type === "sync.state") {
      var was = shared + ":" + incoming, team = shared;
      shared = !!e.shared; incoming = +e.incoming || 0;
      if (was !== shared + ":" + incoming) missing.forEach(showMissing);
      if (shared && !team) { holdsAsk = "ducky-live-holds-" + (++n); req("live.holds", {}, holdsAsk); }
      if (!shared) { far = []; if (!on) mine = {}; paint(); }
    } else if (e.type === "plugin_scope_changed" && e.reason === "synced") {
      var again = missing; missing = [];
      again.forEach(function (el) {
        if (stale(el)) return;
        if (el.tagName === "VIDEO") { el.removeAttribute("poster"); el.load(); return; }
        var src = el.getAttribute("data-ducky-src");
        el.removeAttribute("data-ducky-src");
        if (src) el.setAttribute("src", src);  // fails again: back on the list
      });
    }
  });
})();</script>"""

FILES: dict[str, tuple[str, str]] = {
    "ducky.css": (CSS, "text/css; charset=utf-8"),
    "ducky.js": (JS, "text/javascript; charset=utf-8"),
}
