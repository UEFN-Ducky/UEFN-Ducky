/**
 * Settings → LLMs → Default models: every default in one card. The model for each job (chats
 * and duckies, live voice, spoken summaries, memory summaries; the same list as the welcome
 * setup's "Your defaults") and who makes pictures, 3D models, videos and music when nothing
 * else is picked. A maker row shows only for what a plugin installed here can make.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { runBridgeJob } from "../../hooks/bridgeJobAsync";
import { getApi } from "../../hooks/usePanelApi";
import { fetchStoreCatalog } from "../../hooks/storeCatalogCache";
import { onApiReady } from "../../hooks/onApiReady";
import { subscribePanelPush } from "../../hooks/usePanelPushBus";
import { Icons } from "../../icons/Icons";
import { DuckyModelPicker } from "../../components/ducky/DuckyModelPicker";
import { loadHostMakers, MediaMakerPicker, type HostMediaKind, type Maker } from "../../components/MediaMakerPicker";
import { loadJobModels, MODEL_JOBS, NO_JOB_MODELS, saveJobModel, type JobModels, type ModelJob } from "../../components/modelJobs";
import { consumeDefaultModelHighlight, HIGHLIGHT_DEFAULT_MODEL_EVENT } from "../../navigation/openSettingsTab";
import { unsetDefaultGatewayIcons, type GatewayIcon } from "./defaultModelGateways";

/** Makers the host lists itself: a row only when a plugin installed here makes that kind. */
const HOST_MAKERS: ReadonlyArray<readonly [HostMediaKind, string]> = [["video", "Videos"], ["music", "Music"]];

export function DefaultModelsCard() {
  const [loaded, setLoaded] = useState(false);
  const [models, setModels] = useState<JobModels>(NO_JOB_MODELS);
  const [hostMakers, setHostMakers] = useState<Partial<Record<HostMediaKind, Maker[]>>>({});
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [highlight, setHighlight] = useState(false);
  const [gatewayIcons, setGatewayIcons] = useState<GatewayIcon[]>([]);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const defaultModel = models.chat;

  useEffect(() => {
    return onApiReady(() => {
      void loadJobModels((patch) => setModels((cur) => ({ ...cur, ...patch }))).finally(() => setLoaded(true));
      void loadHostMakers().then(setHostMakers);
    });
  }, []);

  useEffect(() => {
    let timer = 0;
    const pulse = () => {
      setHighlight(true);
      cardRef.current?.scrollIntoView({ block: "center", inline: "nearest" });
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setHighlight(false), 2600);
    };
    if (consumeDefaultModelHighlight()) pulse();
    const onHighlight = () => {
      consumeDefaultModelHighlight();
      pulse();
    };
    window.addEventListener(HIGHLIGHT_DEFAULT_MODEL_EVENT, onHighlight);
    return () => {
      window.removeEventListener(HIGHLIGHT_DEFAULT_MODEL_EVENT, onHighlight);
      window.clearTimeout(timer);
    };
  }, []);

  const loadGatewayIcons = useCallback(async () => {
    const api = getApi();
    if (!api?.duckyos_store_catalog) return;
    try {
      const catalog = await fetchStoreCatalog(api);
      setGatewayIcons(unsetDefaultGatewayIcons(catalog?.items || []));
    } catch {
      /* catalog can be offline — the picker still works */
    }
  }, []);

  useEffect(() => {
    if (!loaded || defaultModel.trim()) return;
    void loadGatewayIcons();
  }, [loaded, defaultModel, loadGatewayIcons]);

  useEffect(() => {
    return subscribePanelPush((event) => {
      if (event.type !== "uefn_plugins_changed") return;
      void loadGatewayIcons();
      void loadHostMakers().then(setHostMakers); // a plugin just installed may make videos
    });
  }, [loadGatewayIcons]);

  const installGateway = async (slug: string) => {
    if (busySlug) return;
    setBusySlug(slug);
    try {
      const result = await runBridgeJob<{ ok?: boolean }>("duckyos_store_download", [slug, "", false], 180_000);
      if (result?.ok) setGatewayIcons((rows) => rows.filter((row) => row.slug !== slug));
    } catch {
      /* leave the icon so they can retry */
    } finally {
      setBusySlug(null);
    }
  };

  const save = async (id: ModelJob["id"], next: string) => {
    setModels((cur) => ({ ...cur, [id]: next }));
    setSaveStatus("saving");
    try {
      await saveJobModel(id, next);
      setSaveStatus("saved");
      window.setTimeout(() => setSaveStatus((s) => (s === "saved" ? "idle" : s)), 2500);
    } catch {
      setSaveStatus("error");
    }
  };

  const showGatewayIcons = loaded && !defaultModel.trim() && gatewayIcons.length > 0;
  const status = saveStatus === "saving" ? " Saving…" : saveStatus === "saved" ? " Saved." : saveStatus === "error" ? " Could not save." : "";

  return (
    <div ref={cardRef} className={`llms-default-model-card${highlight ? " is-highlight" : ""}`}>
      <div className="llms-default-model-row">
        <h3 className="llms-default-model-title">
          <span className="general-tab-section-icon" aria-hidden>
            <Icons.Brain />
          </span>
          Default models
        </h3>
      </div>
      <p className="llms-default-model-note">
        What runs when a chat, a ducky or a workflow doesn't pick its own. A ducky asks before a picture, 3D model or video
        spends.{status}
      </p>
      <ul className="llms-defaults-list" aria-label="Default models">
        {MODEL_JOBS.map((job) => (
          <li key={job.id} className="llms-defaults-row" aria-label={job.title}>
            <span className="llms-defaults-label" title={job.suggest}>
              {job.title}
            </span>
            <div className="llms-default-model-picker">
              <DuckyModelPicker
                model={models[job.id]}
                onChange={(model) => void save(job.id, model)}
                allowClear
                placeholder={!loaded ? "Loading…" : job.id === "chat" ? "No models" : job.empty}
                label=""
                hint=""
                menuPlacement="bottom"
              />
            </div>
          </li>
        ))}
        {(
          [
            ["image", "Pictures"],
            ["mesh", "3D models"],
          ] as const
        ).map(([kind, label]) => (
          <li key={kind} className="llms-defaults-row" aria-label={label}>
            <span className="llms-defaults-label">{label}</span>
            <MediaMakerPicker kind={kind} label={label} />
          </li>
        ))}
        {HOST_MAKERS.filter(([kind]) => hostMakers[kind]?.length).map(([kind, label]) => (
          <li key={kind} className="llms-defaults-row" aria-label={label}>
            <span className="llms-defaults-label">{label}</span>
            <MediaMakerPicker kind={kind} label={label} known={hostMakers[kind]} />
          </li>
        ))}
      </ul>
      {showGatewayIcons ? (
        <div className="llms-default-model-gateways" role="group" aria-label="Install a gateway">
          {gatewayIcons.map((row) => (
            <button
              key={row.slug}
              type="button"
              className="llms-default-model-gateway"
              aria-label={`Install ${row.slug}`}
              disabled={busySlug !== null}
              onClick={() => void installGateway(row.slug)}
            >
              <img src={row.icon} alt="" draggable={false} />
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
