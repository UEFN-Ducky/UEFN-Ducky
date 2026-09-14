import { SettingsToggleRow } from "./SettingsToggleRow";
import { AppearanceAccordionSplit } from "./AppearanceAccordionSplit";
import { useDockSidePanelMode } from "../../hooks/useDockSidePanelMode";
import { useDockSidebarSettings } from "../../hooks/useDockSidebarSettings";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import { DOCK_PANEL_TAB_META } from "../../workspace/dockPanelTabMeta";
import {
  panelsOnSide,
  sidebarPanelCatalog,
  type DockPanelId,
  type DockPanelMode,
  type DockSide,
} from "../../workspace/workspaceDockStorage";

function PanelSideLayoutControl({ side }: { side: DockSide }) {
  const { mode, setMode } = useDockSidePanelMode(side);

  const onSelect = (next: DockPanelMode) => {
    if (next !== mode) setMode(next);
  };

  const label = side === "left" ? "Left panel" : "Right panel";

  return (
    <div className="appearance-layout-control">
      <h4 className="appearance-category-title">{label}</h4>
      <p className="appearance-layout-control-desc">
        How every panel docked on the {side} rail lays out. Tabs show one panel at a time behind a tab
        bar; stacked shows all of them at once, split by resizable dividers. This follows the side, not
        the panel — drag all four panels to the {side} and they all pick up this mode.
      </p>
      <div
        className="content-search-mode-toggle appearance-layout-toggle"
        role="group"
        aria-label={`${label} layout`}
      >
        <button
          type="button"
          className={`content-search-mode-btn${mode === "tabs" ? " is-active" : ""}`}
          aria-pressed={mode === "tabs"}
          onClick={() => onSelect("tabs")}
        >
          Tabs
        </button>
        <button
          type="button"
          className={`content-search-mode-btn${mode === "stacked" ? " is-active" : ""}`}
          aria-pressed={mode === "stacked"}
          onClick={() => onSelect("stacked")}
        >
          Stacked panels
        </button>
      </div>
    </div>
  );
}

function RailPreview({
  side,
  mode,
  enabled,
  labels,
}: {
  side: DockSide;
  mode: DockPanelMode;
  enabled: boolean;
  labels: [string, string];
}) {
  const [first, second] = labels;
  const label = side === "left" ? "Left sidebar" : "Right sidebar";
  const disabledClass = enabled ? "" : " is-disabled";

  return (
    <div className="appearance-layout-preview-block">
      <span className="appearance-layout-preview-caption">{label}</span>
      <div
        className={`appearance-layout-preview-frame appearance-layout-preview-sidebar appearance-layout-preview-sidebar--${mode}${disabledClass}`}
      >
        {!enabled ? (
          <div className="appearance-layout-preview-panel-body">
            <span className="appearance-layout-preview-stack-label">Hidden</span>
          </div>
        ) : mode === "tabs" ? (
          <>
            <div className="appearance-layout-preview-panel-tabs">
              <span className="is-active">{first}</span>
              <span>{second}</span>
            </div>
            <div className="appearance-layout-preview-panel-body">
              <span className="appearance-layout-preview-line" />
              <span className="appearance-layout-preview-line short" />
              <span className="appearance-layout-preview-line" />
            </div>
          </>
        ) : (
          <div className="appearance-layout-preview-stack">
            <div className="appearance-layout-preview-stack-pane">
              <span className="appearance-layout-preview-stack-label">{first}</span>
              <span className="appearance-layout-preview-line" />
              <span className="appearance-layout-preview-line short" />
            </div>
            <div className="appearance-layout-preview-stack-divider" />
            <div className="appearance-layout-preview-stack-pane">
              <span className="appearance-layout-preview-stack-label">{second}</span>
              <span className="appearance-layout-preview-line" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function previewLabels(snapshot: ReturnType<typeof useDockSidebarSettings>["snapshot"], side: DockSide): [string, string] {
  const ids = panelsOnSide(snapshot, side);
  const titles = ids.map((id) => DOCK_PANEL_TAB_META[id]?.headerTitle ?? id);
  return [titles[0] ?? "Empty", titles[1] ?? titles[0] ?? "Empty"];
}

function AppearanceSidebarPreview() {
  const { snapshot, leftRailEnabled, rightRailEnabled } = useDockSidebarSettings();
  const { mode: leftMode } = useDockSidePanelMode("left");
  const { mode: rightMode } = useDockSidePanelMode("right");

  return (
    <div className="appearance-live-preview">
      <div className="appearance-live-preview-label">Live preview</div>
      <RailPreview
        side="left"
        mode={leftMode}
        enabled={leftRailEnabled}
        labels={previewLabels(snapshot, "left")}
      />
      <RailPreview
        side="right"
        mode={rightMode}
        enabled={rightRailEnabled}
        labels={previewLabels(snapshot, "right")}
      />
    </div>
  );
}

function SidebarRailBlock({
  side,
  catalog,
}: {
  side: DockSide;
  catalog: DockPanelId[];
}) {
  const { isOnSide, setPanelOnSide, setRailEnabled, leftRailEnabled, rightRailEnabled } =
    useDockSidebarSettings();
  const enabled = side === "left" ? leftRailEnabled : rightRailEnabled;
  const title = side === "left" ? "Left sidebar" : "Right sidebar";

  return (
    <div className="appearance-sidebar-rail">
      <SettingsToggleRow
        id={`appearance-sidebar-enable-${side}`}
        label={title}
        description="When off, this rail is gone and its header button is hidden."
        checked={enabled}
        onChange={(on) => setRailEnabled(side, on)}
      />
      <PanelSideLayoutControl side={side} />
      <h4 className="appearance-category-title">Panels</h4>
      <div className="general-tab-toggle-card appearance-sidebar-panel-list">
        {catalog.map((id) => {
          const meta = DOCK_PANEL_TAB_META[id];
          return (
            <SettingsToggleRow
              key={`${side}-${id}`}
              id={`appearance-sidebar-${side}-${id}`}
              label={meta?.headerTitle ?? id}
              checked={isOnSide(id, side)}
              onChange={(on) => setPanelOnSide(id, on ? side : null)}
            />
          );
        })}
      </div>
    </div>
  );
}

export function AppearanceSidebarSectionBlock() {
  const contrib = usePluginContributions();
  const catalog = sidebarPanelCatalog(contrib.dock_panels.map((panel) => panel.id));

  return (
    <AppearanceAccordionSplit preview={<AppearanceSidebarPreview />}>
      <div className="appearance-layout-controls">
        <SidebarRailBlock side="left" catalog={catalog} />
        <SidebarRailBlock side="right" catalog={catalog} />
      </div>
    </AppearanceAccordionSplit>
  );
}
