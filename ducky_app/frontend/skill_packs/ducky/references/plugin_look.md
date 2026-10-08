---
description: "Plugin look and style: the Ducky UI kit, every Appearance variable, panel states, focus, hidden tabs and themes"
metadata:
  order: 4
  label: "Plugins: look and style"
  default_enabled: false
  load_condition: "Building or changing a plugin panel, dock, file editor, theme or any plugin HTML/CSS/JS"
---

# Plugin look and style

A plugin panel must look like the rest of Ducky and follow the user's theme. Two
rules cover it: **link the UI kit**, and **color only with Appearance variables**.
`ducky_plugin_validate` enforces both and says how to fix each problem.

## 1. Link the UI kit (one tag)

```html
<script src="../../_kit/ducky.js"></script>
```

- Put it in the `<head>` of every panel page. The path is relative: from
  `ui/index.html` it is `../../_kit/ducky.js`; add one `../` per folder deeper
  (`ui/views/a.html` → `../../../_kit/ducky.js`). Never an absolute `/plugin-ui/…`
  path: some panels are served from a subfolder.
- It links `ducky.css`, applies the user's Appearance variables on load and on every
  change, and marks the page hidden while its tab is hidden (kit animations pause).
- Element defaults (`body`, `button`, `input`, `select`, `textarea`, `a`, `code`,
  headings) use `:where()`, so any rule you write wins.
- Linking `ducky.css` alone also works, but then you apply the theme yourself (§4).

| Class | What |
|-------|------|
| `dk-btn` + `dk-btn--primary` / `--secondary` / `--ghost` / `--danger` / `--icon` | Buttons |
| `dk-input`, `dk-select`, `dk-field` (+ `dk-field__label`, `dk-field__hint`) | Inputs, selects, labelled fields |
| `dk-check` (a `<label>` around a checkbox or radio) | Checkboxes and radios |
| `dk-tabs` > `button.dk-tab[aria-selected="true"]` | Tabs |
| `dk-card` (+ `--interactive`, `dk-card__title`) | Cards |
| `dk-table` | Tables |
| `dk-badge` + `--accent` / `--success` / `--warn` / `--danger` / `--info` | Badges |
| `dk-empty` (+ `dk-empty__title`), `dk-loading`, `dk-error` (+ `dk-error__title`) | Empty, loading, error states |
| `dk-page`, `dk-stack`, `dk-row`, `dk-muted`, `dk-dim` | Page padding, vertical/horizontal stacks, quiet text |

Every control gets a visible keyboard focus ring (`var(--border-focus)`).

## 2. Colors: Appearance variables only

- **Never** a color literal in a UI file: no `#hex`, `rgb()`, `rgba()`, `hsl()`,
  `hwb()`, `lab()`, `lch()`, `oklab()`, `oklch()`, no named colors (`white`,
  `black`, `red`, `gray`, …), not in CSS, inline `style=""`, SVG `fill=` /
  `stroke=`, canvas `fillStyle`, or JS strings. `transparent` and `currentColor` are fine.
- **Never** `var(--x)` for a variable the app doesn't set. A typo
  (`var(--font-family)`, `var(--primary)`) fails validation with a "Did you mean".
- You **may** define your own custom properties for layout (`--gap: 8px`,
  `--row-h: 28px`) or as aliases of real ones (`--hl: var(--accent)`), never with a
  color literal.
- Exempt: files the plugin contributes as an app theme (anything listed under an
  `appearance.*` contribution) and vendored code (a `vendor/` folder, `*.min.js`,
  `*.min.css`).
- If the user asks for specific colors, ship them as an Appearance theme
  (`appearance.profiles`, see `plugin_examples_ui`), keep the panel on the
  variables, and say that the panel follows their Appearance setting. Tell the user
  the panel uses their current Appearance theme when you start the UI, or whenever
  you depart from it.

### Every Appearance variable (generated from the app's theme engine)

Use them as `var(--name)`. The validator holds the authoritative list; this is it
as of this app.

**Surfaces:** `bg` `bg-elevated` `bg-hover` `bg-panel` `card` `card-hover`
`sidebar` `header` `panel-header` `dropdown-bg` `input-bg` `overlay`
`overlay-light` `select`

**Text:** `fg` `fg-dim` `fg-inverse` `muted` `text` `text-muted` `text-primary`

**Borders and focus:** `border` `border-light` `border-subtle` `border-focus`

**Accent and controls:** `accent` `accent-hover` `btn-bg` `btn-hover`
`btn-pressed` `tab-active` `tab-hover`

**Status colors:** `red` `red-dim` `danger` `green` `green-dim` `on-green` `amber`
`amber-dim` `on-amber` `yellow` `yellow-dim` `warn` `warn-dim` `blue` `blue-dim`
`purple` `purple-dim`

**Fonts:** `font-ui` `font-ui-size` `font-mono` `font-mono-size` `font-header`
`font-sidebar` `font-settings` `font-controls`

**Shape and depth:** `radius` `radius-sm` `shadow-sm` `shadow-md` `shadow-lg`
`shadow-dropdown` `shadow-glow` `blur-md`

**Chat:** `chat-block-radius` `chat-body-color` `chat-border-color`
`chat-callout-error-background` `chat-callout-error-border`
`chat-callout-error-color` `chat-callout-info-background`
`chat-callout-info-border` `chat-callout-info-color`
`chat-callout-success-background` `chat-callout-success-border`
`chat-callout-success-color` `chat-callout-warn-background`
`chat-callout-warn-border` `chat-callout-warn-color` `chat-code-background`
`chat-code-border-tint` `chat-code-color` `chat-code-tint` `chat-emphasis-color`
`chat-h1-size` `chat-h2-size` `chat-h3-size` `chat-h4-size` `chat-heading-color`
`chat-heading-weight` `chat-inventory-gap` `chat-line-height` `chat-link-color`
`chat-list-color` `chat-muted-color` `chat-paragraph-gap` `chat-ref-actor`
`chat-ref-asset` `chat-ref-device` `chat-ref-field` `chat-ref-file`
`chat-ref-folder` `chat-ref-keyword` `chat-ref-mesh` `chat-ref-name`
`chat-ref-prefab` `chat-ref-tool` `chat-ref-umg` `chat-ref-verse`
`chat-section-gap` `chat-stats-number-color` `chat-stats-size` `chat-surface`
`chat-table-heading-background` `chat-table-heading-color` `chat-text-font`
`chat-text-size`

**Group chat:** `groupchat-accent` `groupchat-bg` `groupchat-composer-bg`
`groupchat-hover` `groupchat-time`

**Terminal:** `terminal-ansi-fg` `terminal-ansi-green` `terminal-ansi-green-dim`
`terminal-ansi-red` `terminal-ansi-yellow` `terminal-bg` `terminal-cursor`
`terminal-fg` `terminal-selection`

**Verse editor:** `verse-editor-bg` `verse-editor-bracket-match-bg`
`verse-editor-bracket-match-border` `verse-editor-cursor` `verse-editor-fg`
`verse-editor-gutter` `verse-editor-indent` `verse-editor-indent-active`
`verse-editor-line-highlight` `verse-editor-line-highlight-border`
`verse-editor-line-number` `verse-editor-line-number-active`
`verse-editor-selection` `verse-editor-selection-highlight`
`verse-editor-selection-inactive` `verse-editor-widget-bg`
`verse-editor-widget-border`

**Verse syntax:** `verse-syntax-bracket` `verse-syntax-comment`
`verse-syntax-error` `verse-syntax-identifier` `verse-syntax-keyword`
`verse-syntax-number` `verse-syntax-operator` `verse-syntax-operator-arith`
`verse-syntax-operator-compare` `verse-syntax-operator-logical`
`verse-syntax-path` `verse-syntax-string` `verse-syntax-string-char`
`verse-syntax-string-escape` `verse-syntax-tag` `verse-syntax-type`
`verse-syntax-type-id`

**Workflows canvas:** `wf-canvas` `wf-grid-major` `wf-grid-minor` `wf-grid-size`
`wf-group` `wf-light-off` `wf-light-on` `wf-list-text-size` `wf-node-action`
`wf-node-agent` `wf-node-body` `wf-node-border` `wf-node-end` `wf-node-function`
`wf-node-input` `wf-node-logic` `wf-node-mark-opacity` `wf-node-muted`
`wf-node-starter` `wf-node-text` `wf-node-text-size` `wf-node-title-size`
`wf-panel` `wf-panel-blur` `wf-pin-any` `wf-pin-audio` `wf-pin-boolean`
`wf-pin-file` `wf-pin-image` `wf-pin-json` `wf-pin-mesh` `wf-pin-number`
`wf-pin-text` `wf-pin-video` `wf-run` `wf-run-done` `wf-run-path` `wf-select`
`wf-wire-done` `wf-wire-each` `wf-wire-false` `wf-wire-glow` `wf-wire-true`
`wf-wire-width`

Which to reach for: page `bg`, raised blocks `card` (hover `card-hover`), lines
`border`, body text `fg`, secondary text `fg-dim`, hints `muted`, primary action
`accent` with `fg-inverse` text, errors `red` / `red-dim`, success `green` /
`green-dim`, warnings `warn` / `warn-dim`, code `font-mono`. Canvas and charts read
the same variables at draw time: `getComputedStyle(document.documentElement)
.getPropertyValue("--accent")`, and redraw on `appearance_theme`.

## 3. Panel UX every panel needs

- **States:** a loading state (`dk-loading`) while the first call runs, an empty
  state (`dk-empty` with one line saying what to do), and an error state
  (`dk-error` with the message the backend returned). Never a blank panel.
- **Focus:** the kit's ring, or your own `:focus-visible` rule on every control:
  `button:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }`.
  A panel with neither fails validation.
- **Same functions as the tools:** every button calls a `plugin.call` RPC that runs
  the same function as an `@api.tool()`, so a chat can do everything the panel does.
- **Toggles live in Settings:** a plugin switch or option is a `settings.sections`
  property (native Ducky settings), not a custom form inside the panel.
- **Text:** plain words, sentence case, short labels ("Add card", not "SUBMIT").
- **Size:** panels open as editor tabs or docks of any width: use flex/grid and
  `min-width: 0`, never fixed pixel page widths.

## 4. Theme by hand (only without the kit)

`theme.get` returns `{ vars }` and the host pushes
`{ channel: "uefn-plugin-ui", event: { type: "appearance_theme", vars } }` on load
and on every Appearance change. **Keys come without `--`**:

```js
function applyTheme(vars) {
  for (const [key, value] of Object.entries(vars || {})) {
    if (typeof value === "string") document.documentElement.style.setProperty("--" + key, value);
  }
}
```

The kit already does this; prefer the kit.

## 5. Hidden tabs and floating windows

- A plugin tab stays alive (hidden) when the user switches to another tab. The host
  pushes `{ event: { type: "panel.visibility", visible } }` on every show and hide
  and on load. Pause timers, polling, animation loops, audio and video while
  `visible` is false; `document.visibilityState` does **not** change.
- Moving a tab to a floating window (or back) **reloads the page** there. Keep
  anything that must survive in `prefs.set` (small UI choices) or `data.put`
  (records), never only in page memory, and restore it on load.

```js
window.addEventListener("message", (ev) => {
  const e = ev.data?.channel === "uefn-plugin-ui" ? ev.data.event : null;
  if (e?.type === "panel.visibility") e.visible ? resume() : pause();
  if (e?.type === "plugin_scope_changed") reload();  // data scope switched or synced
});
```

## 6. Themes, skins and effects (app-wide)

| Contribution | What it does |
|---|---|
| `appearance.profiles` | A theme in Settings → Appearance: `foundation` (`accent`, `bg`, `surface`, `text`, `border`) plus `overrides` keyed by variable name without `--`. Colors are allowed here. |
| `appearance.css` | `[{ "entry": "theme/extra.css" }]`: extra CSS for the app shell. Use variables; never set `:root` colors from scripts. |
| `appearance.effects` | `[{ "id", "label", "entry" }]`: an optional visual effect the user turns on. |
| `appearance.skin` | `[{ "id", "label", "entry", "css"? }]`: a skin layered on the app. |

The user picks the active theme, skin and effect in Settings → Appearance; a plugin
can't switch them. A theme plugin still ships an `@api.tool()` that lists its themes
and shows the user where to pick one (`ducky_ui_show`, target
`settings.tab.appearance`), a workflow node and a bundled skill. See
`plugin_examples_ui` § Theme.
