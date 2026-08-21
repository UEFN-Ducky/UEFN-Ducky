import { getApi } from "../../hooks/usePanelApi";
import { useProjectFilesSettings } from "../../contexts/ProjectFilesSettingsContext";
import { useUiTarget } from "../../ui-targets/registry";
import { redoAppWalkthrough } from "../../walkthrough";
import { AppSection } from "./AppSection";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";

function ProjectFilesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
    </svg>
  );
}

function PlugIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M12 22v-5" />
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a6 6 0 01-12 0V8z" />
    </svg>
  );
}

function HardDriveIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <line x1="22" y1="12" x2="2" y2="12" />
      <path d="M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z" />
      <line x1="6" y1="16" x2="6.01" y2="16" />
      <line x1="10" y1="16" x2="10.01" y2="16" />
    </svg>
  );
}

function PowerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M18.36 6.64a9 9 0 11-12.73 0" />
      <line x1="12" y1="2" x2="12" y2="12" />
    </svg>
  );
}

function TourIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </svg>
  );
}

export function AddToUefnTab() {
  const {
    showHiddenFiles,
    loaded: hiddenFilesLoaded,
    setShowHiddenFiles,
  } = useProjectFilesSettings();

  const projectFilesRef = useUiTarget("settings.general.project_files", {
    kind: "settings_field",
    label: "Project Files",
    route: "settings.general",
  });
  const addToUefnRef = useUiTarget("settings.general.add_to_uefn", {
    kind: "settings_field",
    label: "Add to UEFN",
    route: "settings.general",
  });
  const appDataRef = useUiTarget("settings.general.app_data", {
    kind: "settings_field",
    label: "App Data",
    route: "settings.general",
  });

  const handleExitAll = async () => {
    const api = getApi();
    if (!api) return;
    try {
      await api.exit_all();
    } catch {
      // force-exit timer still runs on the Python side
    }
  };

  return (
    <div className="general-tab-shell">
      <h2 className="general-tab-page-title">General</h2>

      <AppSection />

      <hr className="general-tab-divider" />

      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<TourIcon />}
          title="Walkthrough"
          description="Replay the first-run tour of the app layout, Settings tabs, Store, and LLM keys."
        />
        <div className="general-tab-btn-row" style={{ marginTop: 4 }}>
          <button
            type="button"
            className="settings-btn general-tab-btn-primary"
            onClick={() => void redoAppWalkthrough()}
          >
            Replay app walkthrough
          </button>
        </div>
      </section>

      <hr className="general-tab-divider" />

      <section className="general-tab-section" ref={projectFilesRef}>
        <GeneralSectionHeader
          icon={<ProjectFilesIcon />}
          title="Project Files"
          description="Sidebar file tree. Engine folders stay hidden by default; init_unreal.py stays visible and read-only."
        />
        <div className="general-tab-toggle-card">
          <SettingsToggleRow
            id="toggle-hidden"
            label="Show hidden project files"
            description="Reveal external actors, collections, maps, and other engine folders in the file tree."
            checked={showHiddenFiles}
            disabled={!hiddenFilesLoaded}
            onChange={(checked) => void setShowHiddenFiles(checked)}
          />
        </div>
      </section>

      <hr className="general-tab-divider" />

      <section className="general-tab-section" ref={addToUefnRef}>
        <GeneralSectionHeader
          icon={<PlugIcon />}
          title="Add to UEFN"
          description={
            <>
              <span className="general-tab-badge">Automatic</span>
              The listener bootstrap installs itself when you open a project or start this app.
              There is no manual Install button — it has to stay automatic.
            </>
          }
        />
        <div className="add-to-uefn-auto-card">
          <p className="add-to-uefn-auto-lead">
            You do not deploy from here. Ducky writes <code>init_unreal.py</code> into the active
            project on open / app start, then UEFN loads the listener from AppData.
          </p>
          <h4 className="add-to-uefn-steps-title">What you need to do</h4>
          <ol className="add-to-uefn-steps">
            <li>
              In UEFN, open <strong>Editor Preferences</strong> → <strong>Experimental</strong> and
              enable <strong>Python scripting</strong> (Python Editor Script Plugin).
            </li>
            <li>
              <strong>Restart UEFN</strong> after enabling Python — required before the listener can
              start.
            </li>
            <li>Open your island / project with UEFN-Ducky running.</li>
            <li>
              Check the duck connection icon in the top bar — it should show{" "}
              <strong>Listener online</strong>. There is no separate Test connection button in
              Settings; that icon is the connection check.
            </li>
          </ol>
        </div>
      </section>

      <hr className="general-tab-divider" />

      <div className="general-tab-footer-grid">
        <section className="general-tab-footer-card" ref={appDataRef}>
          <GeneralSectionHeader
            icon={<HardDriveIcon />}
            title="App Data"
            description="Local settings and cache."
          />
          <button
            type="button"
            className="settings-btn"
            onClick={() => {
              const api = getApi();
              if (api) void api.open_appdata();
            }}
          >
            Open folder
          </button>
        </section>

        <section className="general-tab-footer-card general-tab-footer-card--danger">
          <GeneralSectionHeader
            icon={<PowerIcon />}
            title="Exit All Processes"
            description="Quit every Ducky process. Closing the window only hides to tray."
          />
          <button type="button" className="settings-btn general-tab-btn-danger no-drag" onClick={() => void handleExitAll()}>
            Force Exit All
          </button>
        </section>
      </div>
    </div>
  );
}
