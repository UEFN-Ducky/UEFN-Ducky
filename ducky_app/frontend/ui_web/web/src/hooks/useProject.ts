import { useEffect, useRef, useState } from "react";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { subscribePanelPush } from "./usePanelPushBus";
import { observeProjectRoot } from "./projectSwitchController";
import type { ProjectInfo } from "../types/panel";

const EMPTY: ProjectInfo = { path: "", name: "No project", slug: "_no_project" };

/** Route every project-info update through the switch controller before storing it, so a
 * root change fires the centralized per-project resets exactly once at the earliest signal. */
function applyProjectInfo(setProject: (p: ProjectInfo) => void, info: ProjectInfo): void {
  observeProjectRoot(info.path ?? "");
  setProject(info);
}

export function useProject(pollMs = 15000, refreshToken = 0, onRemoteChange?: () => void) {
  const [project, setProject] = useState<ProjectInfo>(EMPTY);
  const apiRef = useRef<ReturnType<typeof getApi>>(null);
  const inFlightRef = useRef(false);

  useEffect(() => {
    let pollId: number | undefined;
    let started = false;

    const cleanupWait = onApiReady((api) => {
      if (started) return;
      started = true;
      apiRef.current = api;

      const poll = () => {
        if (inFlightRef.current) return;
        inFlightRef.current = true;
        void api
          .get_project_info()
          .then((info) => applyProjectInfo(setProject, info))
          .finally(() => {
            inFlightRef.current = false;
          });
      };

      poll();
      pollId = window.setInterval(poll, pollMs);
    });

    return () => {
      cleanupWait();
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [pollMs]);

  useEffect(() => {
    if (refreshToken === 0) return;
    const api = apiRef.current ?? getApi();
    if (!api || inFlightRef.current) return;

    inFlightRef.current = true;
    void api
      .get_project_info()
      .then((info) => applyProjectInfo(setProject, info))
      .finally(() => {
        inFlightRef.current = false;
      });
  }, [refreshToken]);

  useEffect(() => {
    return subscribePanelPush((event) => {
      if (event.type !== "project_changed" || !event.project) return;
      applyProjectInfo(setProject, event.project);
      onRemoteChange?.();
    });
  }, [onRemoteChange]);

  return project;
}
