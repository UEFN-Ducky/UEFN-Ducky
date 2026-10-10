import { useEffect, useRef, useState } from "react";
import { getApi } from "./usePanelApi";
import { onApiReady } from "./onApiReady";
import { subscribePanelPush } from "./usePanelPushBus";
import { observeProjectRoot } from "./projectSwitchController";
import type { ProjectInfo } from "../types/panel";
import { setProjectContentRoot } from "../verse-editor/utils/isVerseFile";
import { setVisibleInterval } from "../utils/visibleInterval";

const EMPTY: ProjectInfo = { path: "", name: "No project", slug: "_no_project" };
export const PROJECT_SELECTED_EVENT = "ducky:project-selected";

/** Route every project-info update through the switch controller before storing it, so a
 * root change fires the centralized per-project resets exactly once at the earliest signal. */
function applyProjectInfo(
  setProject: (p: ProjectInfo) => void,
  info: ProjectInfo,
  appliedRef: { current: ProjectInfo | null },
): void {
  // Every poll returns a new object; storing an unchanged one re-rendered the whole app.
  if (sameProjectInfo(appliedRef.current, info)) return;
  appliedRef.current = info;
  observeProjectRoot(info.path ?? "");
  // Before the tree re-renders: a folder project's Content pane is the folder itself.
  setProjectContentRoot(info.content_root);
  setProject(info);
}

function sameProjectInfo(a: ProjectInfo | null, b: ProjectInfo): boolean {
  return !!a && a.path === b.path && a.name === b.name && a.slug === b.slug
    && a.kind === b.kind && a.content_root === b.content_root;
}

export function useProject(pollMs = 15000, refreshToken = 0, onRemoteChange?: () => void) {
  const [project, setProject] = useState<ProjectInfo>(EMPTY);
  const apiRef = useRef<ReturnType<typeof getApi>>(null);
  const inFlightRef = useRef(false);
  const revisionRef = useRef(0);
  const projectRef = useRef(project);
  projectRef.current = project;
  const appliedRef = useRef<ProjectInfo | null>(null);

  useEffect(() => {
    let stopPoll: (() => void) | undefined;
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
            if (!disposed && revision === revisionRef.current && info) applyProjectInfo(setProject, info, appliedRef);
          })
          .catch(() => { /* Retry on the next poll. */ })
          .finally(() => {
            inFlightRef.current = false;
          });
      };

      poll();
      // Project switches are pushed; the poll only catches up, so it can rest while hidden.
      stopPoll = setVisibleInterval(poll, pollMs);
    });

    return () => {
      disposed = true;
      cleanupWait();
      stopPoll?.();
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
        if (!disposed && revision === revisionRef.current && info) applyProjectInfo(setProject, info, appliedRef);
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
      applyProjectInfo(setProject, info, appliedRef);
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
