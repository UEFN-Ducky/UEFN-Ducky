import { useCallback, useRef } from "react";
import { Icons } from "../icons/Icons";
import { ConnectionStatusIcon } from "../components/ConnectionStatusIcon";
import { getApi } from "../hooks/usePanelApi";
import type { ListenerStatus, ProjectInfo } from "../types/panel";

interface WelcomeViewProps {
  listener: ListenerStatus;
  project: ProjectInfo;
  onProjectChanged?: () => void;
}

export function WelcomeView({ listener, project, onProjectChanged }: WelcomeViewProps) {
  const hasProject = !!project.path?.trim();
  const pickingRef = useRef(false);

  const addProject = useCallback(async () => {
    if (pickingRef.current) return;
    pickingRef.current = true;
    try {
      const api = getApi();
      if (!api) return;
      const path = await api.pick_project_path();
      if (!path) return;
      await api.set_project_root(path);
      onProjectChanged?.();
    } finally {
      pickingRef.current = false;
    }
  }, [onProjectChanged]);

  return (
    <div className="app-drag-surface welcome-view-root">
      <div className="no-drag welcome-view-inner">
        <div>
          <div className="welcome-view-title-row">
            <ConnectionStatusIcon
              isOnline={listener.online}
              isWedged={listener.wedged}
              size={32}
            />
            <h1 className="welcome-view-title">UEFN Ducky</h1>
          </div>
          <p className="welcome-view-version">
            v{listener.version}
            {hasProject ? ` · ${project.name}` : ""}
          </p>
        </div>

        {!hasProject ? (
          <>
            <p className="welcome-view-desc">
              {listener.online
                ? "Agent is running. Add a UEFN project to use duckies, file editing, and map tools."
                : "Open UEFN with the agent listener deployed, then add your project here to start."}
            </p>
            <button type="button" className="settings-btn welcome-view-add-btn" onClick={() => void addProject()}>
              <Icons.Plus />
              Add project…
            </button>
          </>
        ) : (
          <p className="welcome-view-ready-desc">
            Project <strong className="welcome-view-project-name">{project.name}</strong> is ready. Open the ducky view
            from the header.
          </p>
        )}
      </div>
    </div>
  );
}
