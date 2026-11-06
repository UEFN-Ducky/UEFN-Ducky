import { useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { setFirstOpenToursAllowed } from "../../walkthrough/firstOpen";
import { useProjectFilesSettings } from "../../contexts/ProjectFilesSettingsContext";
import { useUiTarget } from "../../ui-targets/registry";
import { requestShowChatComposer } from "../../navigation/openChatComposer";
import { listHostTours, redoTour } from "../../walkthrough";
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

/** Tours on first open, and Ducky's Show me playing by itself. Both on unless switched off. */
function GuidanceSwitches() {
  const [firstOpen, setFirstOpen] = useState(true);
  const [showMe, setShowMe] = useState(true);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings?.()).then((settings) => {
      const s = (settings || {}) as { first_open_tours?: boolean; show_me_autoplay?: boolean };
      setFirstOpen(s.first_open_tours !== false);
      setShowMe(s.show_me_autoplay !== false);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }), []);
  return (
    <div className="general-tab-toggle-card">
      <SettingsToggleRow
        id="toggle-first-open-tours"
        label="Show a tour the first time I open something"
        description="Workflows and other areas start their tour once, the first time they open."
        checked={firstOpen}
        disabled={!loaded}
        onChange={(value) => {
          setFirstOpen(value);
          setFirstOpenToursAllowed(value);
          void getApi()?.save_agent_settings({ first_open_tours: value });
        }}
      />
      <SettingsToggleRow
        id="toggle-show-me-autoplay"
        label="Let Ducky show me things"
        description="Ducky takes you to what it's talking about and highlights it. Off: it only leaves a Show me button in the chat."
        checked={showMe}
        disabled={!loaded}
        onChange={(value) => {
          setShowMe(value);
          void getApi()?.save_agent_settings({ show_me_autoplay: value });
        }}
      />
    </div>
  );
}

/** Start with Windows: hidden in the tray at sign-in, so opening Ducky is instant. On unless switched off. */
export function StartWithWindowsSwitch() {
  const [on, setOn] = useState(true);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings?.()).then((settings) => {
      setOn((settings as { start_with_windows?: boolean } | undefined)?.start_with_windows !== false);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }), []);
  return (
    <div className="general-tab-toggle-card">
      <SettingsToggleRow
        id="toggle-start-with-windows"
        label="Start with Windows"
        description="Ducky opens hidden in the tray when you sign in, so opening it is instant."
        checked={on}
        disabled={!loaded}
        onChange={(value) => {
          setOn(value);
          void getApi()?.save_agent_settings({ start_with_windows: value });
        }}
      />
    </div>
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
      <StartWithWindowsSwitch />

      <hr className="general-tab-divider" />

      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<TourIcon />}
          title="Walkthrough"
          description="Replay one tour at a time — Welcome, Chat, Workflows, or a Settings tab."
        />
        <GuidanceSwitches />
        <div className="walkthrough-list">
          {listHostTours().map((tour) => (
            <div key={tour.id} className="walkthrough-list-row">
              <div className="walkthrough-list-copy">
                <div className="walkthrough-list-title">{tour.title}</div>
                {tour.description ? <div className="walkthrough-list-desc">{tour.description}</div> : null}
              </div>
              <button
                type="button"
                className="settings-btn"
                onClick={() => {
                  if (tour.id === "chat.composer") requestShowChatComposer();
                  void redoTour(tour.id);
                }}
              >
                Replay
              </button>
            </div>
          ))}
        </div>
      </section>

      <hr className="general-tab-divider" />

      <section className="general-tab-section" ref={projectFilesRef}>
        <GeneralSectionHeader
          icon={<ProjectFilesIcon />}
          title="Project Files"
          description="Sidebar file tree. Engine folders stay hidden by default."
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
            You do not deploy from here. Ducky writes <code>Content/Python/init_unreal.py</code>{" "}
            into the active project on open / app start (listener boot). Do not delete that file.
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
