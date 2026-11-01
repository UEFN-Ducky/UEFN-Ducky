---
description: "Plugin look and style: the Ducky UI kit, the app's layout (floating side panels, pill tabs, icon choice rows), every Appearance variable, panel states, focus, hidden tabs and themes"
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
| `dk-layout` > `aside.dk-sidebar` (+ `--right`, `dk-sidebar__title`) + `main.dk-main` | Page layout: floating side panels, a frameless main area (§3) |
| `dk-nav-item` (`aria-current="page"` on the open one) | Icon + label rows in a side panel |
| `dk-btn` + `dk-btn--primary` / `--secondary` / `--ghost` / `--danger` / `--icon` | Buttons |
| `dk-input`, `dk-select`, `dk-field` (+ `dk-field__label`, `dk-field__hint`) | Inputs, selects, labelled fields |
| `dk-choice` (`role="radio"`, `aria-checked`; `dk-choice__icon`, `dk-choice__text`, `dk-choice__hint`) | Picking one of a few things: icon rows with a check, not radio circles |
| `dk-check` (a `<label>` around a checkbox) | Checkboxes (and radios only in dense forms) |
| `dk-tabs` > `button.dk-tab[aria-selected="true"]` | Tabs: rounded pills on the page background |
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

- **Layout, like the app:** side panels are floating cards (a 6px gap of page
  background around them, `1px var(--border)`, `var(--radius-sm)` corners,
  `var(--panel-header)` fill); the main area is the page background itself, with no
  border, frame or fill. Use the kit's `dk-layout` / `dk-sidebar` / `dk-main`:

  ```html
  <div class="dk-layout">
    <aside class="dk-sidebar">
      <div class="dk-sidebar__title">Library</div>
      <button class="dk-nav-item" aria-current="page"><svg>…</svg> Cards</button>
      <button class="dk-nav-item"><svg>…</svg> Packs</button>
    </aside>
    <main class="dk-main dk-page">…</main>
  </div>
  ```

  A **dock** panel already sits inside the app's sidebar card: don't draw another
  outer border or card around it.
- **Rows inside a card** are rounded and inset a few pixels from its edges, so hover,
  selection and the focus ring stay inside the card and never touch its border.
- **Icons:** nav rows, choice rows and list rows lead with a small icon; a bare text
  list is the exception.
- **Picking one of a few things** (a mode, a view, a target) is a column of
  `dk-choice` rows (icon, title, short hint, a check on the picked one), or tiles for
  visual things like screens or sizes. Not radio circles, and not collapsible
  accordions just to group a short list: use a small uppercase section title instead.
- **Tabs** are the kit's pills on the page background: no strip behind them and no
  underline.
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
  if (e?.type === "plugin_scope_changed") reload(e.reason === "synced" ? e.keys : null);  // merge, or start over
});
```

## 6. Themes, skins and effects (app-wide)

| Contribution | What it does |
|---|---|
| `appearance.profiles` | A theme in Settings → Appearance: `foundation` (`accent`, `bg`, `surface`, `text`, `border`) plus `overrides` keyed by variable name without `--`. Colors are allowed here. |
| `appearance.css` | `[{ "entry": "theme/extra.css" }]`: extra CSS for the app shell. Use variables; never set `:root` colors from scripts. |
| `appearance.effects` | `[{ "id", "label", "entry" }]`: an optional visual effect the user turns on. |
| `appearance.skin` | `[{ "id", "label", "entry", "css"? }]`: a skin layered on the app. |

A theme plugin switches Appearance to one of its own profiles with
`api.set_appearance_profile(<profile id>)`, the same as the user picking it in
Settings → Appearance (that also turns on the plugin's skin and effect, if it has
them). It can't pick another plugin's theme or the user's own. It still ships an
`@api.tool()` that lists and applies its themes, a workflow node and a bundled skill.
See `plugin_examples_ui` § Theme.

## 7. Make it look designed (unslop pass)

The kit makes a panel look like Ducky; it doesn't make it look designed. Before
calling a panel done, run the unslop pass: `skill_read_subskill("agent-skills",
"unslop")` (direction, nine passes, checklist; `unslop_signs` is the audit list).
Adapted from unslop by Damir Sokolovsky (@heydamir), CC BY 4.0.

In a Ducky panel its tokens map onto the kit, so you never type its hex values:

| unslop says | In a plugin panel |
|---|---|
| One neutral ramp, three text roles | surfaces `--bg`, `--card`, `--bg-elevated`, `--panel-header`; text `--fg`, `--fg-dim`, `--muted` |
| One accent, used rarely | `--accent` on the primary `dk-btn--primary`, the selection and one key figure only |
| Hairline borders | `1px var(--border)` |
| Semantic colors only for meaning | `--green` / `--red` / `--warn` (+ `-dim`) for success, error, warning, never decoration |
| No glows, quiet depth | no `--shadow-glow` on chrome; `--shadow-sm` at most on floating things |
| 4px spacing scale, one row height | gaps and paddings of 4 / 8 / 12 / 16 / 24; every row in a list one height |
| One icon set, one size | one inline SVG set at 16 px, stroke 1.5, `currentColor` |
| Copy rules | sentence case labels, no `!`, no trailing periods, no greetings, no hype words |
| Motion | color/opacity/transform transitions of 120–220 ms, no `transition: all`, no looping pulse on things that aren't live |

Where the two disagree, this page's house style wins (for example the small
uppercase section titles in §3). Fix every remaining sign before reporting the
panel finished.
