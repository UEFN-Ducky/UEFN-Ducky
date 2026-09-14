import { AppearanceAccordionSplit } from "./AppearanceAccordionSplit";
import { SettingsToggleRow } from "./SettingsToggleRow";
import { useHeaderVisibility } from "../../hooks/useHeaderVisibility";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import {
  resolvePluginHeaderAction,
  sortPluginHeaderButtons,
} from "../../hooks/pluginHeaderActions";
import {
  BUILTIN_HEADER_BUTTONS,
  headerButtonCatalog,
  type HeaderCatalogEntry,
} from "../../workspace/headerVisibilityStorage";

function listedPluginButtons(contrib: ReturnType<typeof usePluginContributions>) {
  return sortPluginHeaderButtons(contrib.header_buttons).filter((btn) => {
    const isTranslation = btn.plugin_id === "translation" || btn.id === "translation";
    return isTranslation || !!resolvePluginHeaderAction(btn.action, btn.plugin_id);
  });
}

function HeaderPreviewChip({ label, on }: { label: string; on: boolean }) {
  return (
    <span className={`appearance-header-preview-chip${on ? "" : " is-off"}`} title={label}>
      {label}
    </span>
  );
}

function AppearanceHeaderPreview({ catalog }: { catalog: HeaderCatalogEntry[] }) {
  const { isVisible } = useHeaderVisibility();
  return (
    <div className="appearance-live-preview">
      <div className="appearance-live-preview-label">Live preview</div>
      <div className="appearance-layout-preview-block">
        <span className="appearance-layout-preview-caption">Header</span>
        <div className="appearance-layout-preview-frame appearance-header-preview-bar">
          {catalog.map((row) => (
            <HeaderPreviewChip key={row.id} label={row.label} on={isVisible(row.id)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function HeaderToggleList({
  title,
  entries,
}: {
  title: string;
  entries: HeaderCatalogEntry[];
}) {
  const { isVisible, setVisible } = useHeaderVisibility();
  if (!entries.length) return null;
  return (
    <div className="appearance-sidebar-rail">
      <h4 className="appearance-category-title">{title}</h4>
      <div className="general-tab-toggle-card appearance-sidebar-panel-list">
        {entries.map((row) => (
          <SettingsToggleRow
            key={row.id}
            id={`appearance-header-${row.id}`}
            label={row.label}
            checked={isVisible(row.id)}
            onChange={(on) => setVisible(row.id, on)}
          />
        ))}
      </div>
    </div>
  );
}

export function AppearanceHeaderSectionBlock() {
  const contrib = usePluginContributions();
  const catalog = headerButtonCatalog(listedPluginButtons(contrib));
  const builtins = catalog.filter((row) => row.group === "builtin");
  const plugins = catalog.filter((row) => row.group === "plugin");

  return (
    <AppearanceAccordionSplit preview={<AppearanceHeaderPreview catalog={catalog} />}>
      <div className="appearance-layout-controls">
        <HeaderToggleList title="Built-in" entries={builtins.length ? builtins : BUILTIN_HEADER_BUTTONS} />
        <HeaderToggleList title="Plugins" entries={plugins} />
      </div>
    </AppearanceAccordionSplit>
  );
}
