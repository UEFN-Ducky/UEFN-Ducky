import { useEffect, useRef, useState } from "react";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { subscribePanelPush } from "./usePanelPushBus";
import { observeProjectRoot } from "./projectSwitchController";
import type { ProjectInfo } from "../types/panel";

const EMPTY: ProjectInfo = { path: "", name: "No project", slug: "_no_project" };
export const PROJECT_SELECTED_EVENT = "ducky:project-selected";

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
  const revisionRef = useRef(0);
  const projectRef = useRef(project);
  projectRef.current = project;

  useEffect(() => {
    let pollId: number | undefined;
    let started = false;
    let disposed = false;

    const cleanupWait = onApiReady((api) => {
      if (started) return;
      started = true;
      apiRef.current = api;

      const poll = () => {
        if (inFlightRef.current) return;
        inFlightRef.current = true;
        const revision = revisionRef.current;
        void api
          .get_project_info()
          .then((info) => {
            if (!disposed && revision === revisionRef.current && info) applyProjectInfo(setProject, info);
          })
          .catch(() => { /* Retry on the next poll. */ })
          .finally(() => {
            inFlightRef.current = false;
          });
      };

      poll();
      pollId = window.setInterval(poll, pollMs);
    });

    return () => {
      disposed = true;
      cleanupWait();
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [pollMs]);

  useEffect(() => {
    if (refreshToken === 0) return;
    const api = apiRef.current ?? getApi();
    if (!api || inFlightRef.current) return;

    inFlightRef.current = true;
    const revision = revisionRef.current;
    let disposed = false;
    void api
      .get_project_info()
      .then((info) => {
        if (!disposed && revision === revisionRef.current && info) applyProjectInfo(setProject, info);
      })
      .catch(() => { /* A push or the next poll can retry. */ })
      .finally(() => {
        inFlightRef.current = false;
      });
    return () => { disposed = true; };
  }, [refreshToken]);

  useEffect(() => {
    const apply = (info: ProjectInfo) => {
      revisionRef.current++;
      const changed = projectRef.current.path !== info.path;
      projectRef.current = info;
      applyProjectInfo(setProject, info);
      if (changed) onRemoteChange?.();
    };
    const selected = (event: Event) => apply((event as CustomEvent<ProjectInfo>).detail);
    window.addEventListener(PROJECT_SELECTED_EVENT, selected);
    const unsubscribe = subscribePanelPush((event) => {
      if (event.type !== "project_changed" || !event.project) return;
      apply(event.project);
    });
    return () => {
      unsubscribe();
      window.removeEventListener(PROJECT_SELECTED_EVENT, selected);
    };
  }, [onRemoteChange]);

  return project;
}
