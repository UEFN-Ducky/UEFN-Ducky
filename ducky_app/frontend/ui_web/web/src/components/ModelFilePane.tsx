import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { MTLLoader } from "three/examples/jsm/loaders/MTLLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ColladaLoader } from "three/examples/jsm/loaders/ColladaLoader.js";

import { onApiReady } from "../hooks/onApiReady";
import { useWatchProjectFile } from "../hooks/useWatchProjectFile";
import { basename } from "../verse-editor/utils/isVerseFile";
import { ChoiceDropdown } from "./ChoiceDropdown";
import { collectMaterials, type ModelMaterialInfo } from "./modelMaterials";

import "./model-preview.css";

interface ModelFilePaneProps {
  relativePath: string;
  /** When set, skip project-file lookup and load this cached/media source directly. */
  resolvedMedia?: ModelUrls | null;
  /** Optional read-only metadata (kept for callers; not shown in the chrome). */
  meshMetadata?: Record<string, unknown> | null;
  /** Cap DPR lower for heavier UEFN exports (default 2). */
  maxPixelRatio?: number;
  /** Extra controls rendered in the top toolbar (e.g. Open in UEFN). */
  toolbarExtras?: ReactNode;
}

type ModelUrls = {
  media_url: string;
  media_base_url: string;
  media_filename: string;
  mime: string;
};

type LoadedModel = {
  object: THREE.Object3D;
  animations: THREE.AnimationClip[];
};

type PreviewMode = "model" | "material";

type SceneHandle = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  grid: THREE.Object3D;
  root: THREE.Object3D;
  hemi: THREE.HemisphereLight;
  key: THREE.DirectionalLight;
  fill: THREE.DirectionalLight;
  mixer: THREE.AnimationMixer | null;
  clips: THREE.AnimationClip[];
  action: THREE.AnimationAction | null;
  sphere: THREE.Mesh | null;
  skeleton: THREE.SkeletonHelper | null;
  lastTime: number;
  speed: number;
  loop: boolean;
  scrubbing: boolean;
};

const LIGHT_DEFAULTS = {
  ambient: 1.1,
  key: 1.2,
  fill: 0.45,
  azimuth: 39,
  elevation: 52,
};

const KEY_LIGHT_RADIUS = 10;

function positionKeyLight(light: THREE.DirectionalLight, azimuthDeg: number, elevationDeg: number) {
  const az = (azimuthDeg * Math.PI) / 180;
  const el = (elevationDeg * Math.PI) / 180;
  const horizontal = Math.cos(el) * KEY_LIGHT_RADIUS;
  light.position.set(
    Math.sin(az) * horizontal,
    Math.sin(el) * KEY_LIGHT_RADIUS,
    Math.cos(az) * horizontal,
  );
}

function applyWireframe(root: THREE.Object3D, on: boolean) {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      const anyMat = mat as (THREE.Material & { wireframe?: boolean }) | null;
      if (anyMat && "wireframe" in anyMat) {
        anyMat.wireframe = on;
        anyMat.needsUpdate = true;
      }
    }
  });
}

function formatAnimTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0.00s";
  return `${seconds.toFixed(2)}s`;
}

function suffixOf(path: string): string {
  const name = basename(path).toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot) : "";
}

function fitCameraToObject(camera: THREE.PerspectiveCamera, object: THREE.Object3D, controls: OrbitControls) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const fov = (camera.fov * Math.PI) / 180;
  let distance = maxDim / (2 * Math.tan(fov / 2));
  distance *= 1.6;
  camera.position.set(center.x + distance * 0.6, center.y + distance * 0.45, center.z + distance);
  camera.near = Math.max(distance / 100, 0.01);
  camera.far = Math.max(distance * 100, 1000);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

function makeUrlModifier(baseUrl: string) {
  return (url: string) => {
    if (/^(?:https?:|blob:|data:)/i.test(url)) return url;
    const clean = url.replace(/^\.\//, "");
    return `${baseUrl}${encodeURIComponent(clean.split("/").pop() || clean)}`;
  };
}

// Shared worker pool — avoid spawning a new Draco decoder per GLB drop.
let sharedDracoLoader: DRACOLoader | null = null;

function getDracoLoader(): DRACOLoader {
  if (!sharedDracoLoader) {
    sharedDracoLoader = new DRACOLoader();
    // vite base "./" + public/draco (ensure-draco-assets.mjs)
    sharedDracoLoader.setDecoderPath("./draco/");
  }
  return sharedDracoLoader;
}

function loadModel(suffix: string, baseUrl: string, filename: string): Promise<LoadedModel> {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(makeUrlModifier(baseUrl));

  return new Promise((resolve, reject) => {
    const onError = (err: unknown) => reject(err instanceof Error ? err : new Error(String(err)));

    if (suffix === ".glb" || suffix === ".gltf") {
      const loader = new GLTFLoader(manager);
      loader.setDRACOLoader(getDracoLoader());
      loader.setPath(baseUrl);
      loader.load(
        filename,
        (gltf) => resolve({ object: gltf.scene, animations: gltf.animations ?? [] }),
        undefined,
        onError,
      );
      return;
    }
    if (suffix === ".fbx") {
      const loader = new FBXLoader(manager);
      loader.setPath(baseUrl);
      loader.load(
        filename,
        (obj) => resolve({ object: obj, animations: obj.animations ?? [] }),
        undefined,
        onError,
      );
      return;
    }
    if (suffix === ".obj") {
      const mtlName = filename.replace(/\.obj$/i, ".mtl");
      const mtlLoader = new MTLLoader(manager);
      mtlLoader.setPath(baseUrl);
      mtlLoader.load(
        mtlName,
        (materials) => {
          materials.preload();
          const objLoader = new OBJLoader(manager);
          objLoader.setMaterials(materials);
          objLoader.setPath(baseUrl);
          objLoader.load(filename, (obj) => resolve({ object: obj, animations: [] }), undefined, onError);
        },
        undefined,
        () => {
          const objLoader = new OBJLoader(manager);
          objLoader.setPath(baseUrl);
          objLoader.load(filename, (obj) => resolve({ object: obj, animations: [] }), undefined, onError);
        },
      );
      return;
    }
    if (suffix === ".stl") {
      const loader = new STLLoader(manager);
      loader.setPath(baseUrl);
      loader.load(
        filename,
        (geometry) => {
          const material = new THREE.MeshStandardMaterial({
            name: "Default",
            color: 0xb0b8c0,
            metalness: 0.1,
            roughness: 0.75,
          });
          resolve({ object: new THREE.Mesh(geometry, material), animations: [] });
        },
        undefined,
        onError,
      );
      return;
    }
    if (suffix === ".ply") {
      const loader = new PLYLoader(manager);
      loader.setPath(baseUrl);
      loader.load(
        filename,
        (geometry) => {
          geometry.computeVertexNormals();
          const material = new THREE.MeshStandardMaterial({
            name: "Default",
            color: 0xb0b8c0,
            metalness: 0.1,
            roughness: 0.75,
          });
          resolve({ object: new THREE.Mesh(geometry, material), animations: [] });
        },
        undefined,
        onError,
      );
      return;
    }
    if (suffix === ".dae") {
      const loader = new ColladaLoader(manager);
      loader.setPath(baseUrl);
      loader.load(
        filename,
        (collada) => {
          if (!collada?.scene) {
            reject(new Error("Collada file had no scene"));
            return;
          }
          resolve({
            object: collada.scene,
            animations: (collada as { animations?: THREE.AnimationClip[] }).animations ?? [],
          });
        },
        undefined,
        onError,
      );
      return;
    }
    reject(new Error(`Unsupported 3D format: ${suffix || "(none)"}`));
  });
}

function MaterialSwatch({ colorHex }: { colorHex: string | null }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (colorHex) el.style.setProperty("--mat-swatch", colorHex);
    else el.style.removeProperty("--mat-swatch");
  }, [colorHex]);
  return (
    <span
      ref={ref}
      className="model-material-swatch"
      data-has-color={colorHex ? "1" : "0"}
      aria-hidden
    />
  );
}

function findMaterialById(root: THREE.Object3D, id: string): THREE.Material | null {
  let found: THREE.Material | null = null;
  root.traverse((obj) => {
    if (found) return;
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      if (mat?.uuid === id) {
        found = mat;
        return;
      }
    }
  });
  return found;
}

function clearMaterialDims(root: THREE.Object3D) {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      if (!mat) continue;
      const anyMat = mat as THREE.Material & { _duckyOrigOpacity?: number; _duckyOrigTransparent?: boolean };
      if (anyMat._duckyOrigOpacity === undefined) continue;
      mat.opacity = anyMat._duckyOrigOpacity;
      mat.transparent = anyMat._duckyOrigTransparent ?? false;
      mat.needsUpdate = true;
    }
  });
}

function applyMaterialHighlight(root: THREE.Object3D, selectedId: string | null) {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const usesSelected = selectedId ? mats.some((m) => m && m.uuid === selectedId) : false;
    const setOpacity = (mat: THREE.Material, dim: boolean) => {
      const anyMat = mat as THREE.Material & { _duckyOrigOpacity?: number; _duckyOrigTransparent?: boolean };
      if (anyMat._duckyOrigOpacity === undefined) {
        anyMat._duckyOrigOpacity = mat.opacity;
        anyMat._duckyOrigTransparent = mat.transparent;
      }
      if (dim) {
        mat.transparent = true;
        mat.opacity = Math.min(anyMat._duckyOrigOpacity ?? 1, 0.18);
        mat.needsUpdate = true;
      } else {
        mat.opacity = anyMat._duckyOrigOpacity ?? 1;
        mat.transparent = anyMat._duckyOrigTransparent ?? false;
        mat.needsUpdate = true;
      }
    };
    for (const mat of mats) {
      if (!mat) continue;
      setOpacity(mat, Boolean(selectedId) && !usesSelected);
    }
  });
}

export function ModelFilePane({
  relativePath,
  resolvedMedia = null,
  meshMetadata: _meshMetadata = null,
  maxPixelRatio = 2,
  toolbarExtras = null,
}: ModelFilePaneProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const handleRef = useRef<SceneHandle | null>(null);
  const scrubRef = useRef<HTMLInputElement | null>(null);
  const timeLabelRef = useRef<HTMLSpanElement | null>(null);
  const pauseRef = useRef(false);
  const [urls, setUrls] = useState<ModelUrls | null>(resolvedMedia);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!resolvedMedia);
  const [, setStatus] = useState("Loading model…");
  const [materials, setMaterials] = useState<ModelMaterialInfo[]>([]);
  const [selectedMatId, setSelectedMatId] = useState<string | null>(null);
  const [showMaterials, setShowMaterials] = useState(false);
  const [showGrid, setShowGrid] = useState(true);
  const [previewMode, setPreviewMode] = useState<PreviewMode>("model");
  const [clipNames, setClipNames] = useState<string[]>([]);
  const [activeClip, setActiveClip] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [loop, setLoop] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [showSkeleton, setShowSkeleton] = useState(false);
  const [clipDuration, setClipDuration] = useState(0);
  const [showLights, setShowLights] = useState(false);
  const [ambientIntensity, setAmbientIntensity] = useState(LIGHT_DEFAULTS.ambient);
  const [keyIntensity, setKeyIntensity] = useState(LIGHT_DEFAULTS.key);
  const [fillIntensity, setFillIntensity] = useState(LIGHT_DEFAULTS.fill);
  const [keyAzimuth, setKeyAzimuth] = useState(LIGHT_DEFAULTS.azimuth);
  const [keyElevation, setKeyElevation] = useState(LIGHT_DEFAULTS.elevation);
  const [autoRotate, setAutoRotate] = useState(false);
  const [wireframe, setWireframe] = useState(false);

  const resolvedMediaUrl = resolvedMedia?.media_url ?? "";
  const resolvedMediaBaseUrl = resolvedMedia?.media_base_url ?? "";
  const resolvedMediaFilename = resolvedMedia?.media_filename ?? "";
  const resolvedMediaMime = resolvedMedia?.mime ?? "";

  const loadMeta = useCallback(
    (options?: { showLoading?: boolean }) => {
      if (resolvedMediaUrl && resolvedMediaBaseUrl && resolvedMediaFilename) {
        const base = resolvedMediaBaseUrl.endsWith("/")
          ? resolvedMediaBaseUrl
          : `${resolvedMediaBaseUrl}/`;
        setUrls((prev) => {
          if (
            prev &&
            prev.media_url === resolvedMediaUrl &&
            prev.media_base_url === base &&
            prev.media_filename === resolvedMediaFilename &&
            prev.mime === resolvedMediaMime
          ) {
            return prev;
          }
          return {
            media_url: resolvedMediaUrl,
            media_base_url: base,
            media_filename: resolvedMediaFilename,
            mime: resolvedMediaMime,
          };
        });
        setLoading(false);
        setError(null);
        return () => undefined;
      }
      const showLoading = options?.showLoading ?? true;
      return onApiReady((api) => {
        if (showLoading) setLoading(true);
        setError(null);
        setMaterials([]);
        setSelectedMatId(null);
        setPreviewMode("model");
        setClipNames([]);
        setActiveClip(0);
        setPlaying(true);
        setLoop(true);
        setSpeed(1);
        setShowSkeleton(false);
        setClipDuration(0);
        setAmbientIntensity(LIGHT_DEFAULTS.ambient);
        setKeyIntensity(LIGHT_DEFAULTS.key);
        setFillIntensity(LIGHT_DEFAULTS.fill);
        setKeyAzimuth(LIGHT_DEFAULTS.azimuth);
        setKeyElevation(LIGHT_DEFAULTS.elevation);
        setAutoRotate(false);
        setWireframe(false);
        const fetchUrl =
          api.project_file_media_url?.(relativePath) ??
          api.read_project_file(relativePath).then((r) => ({
            path: r.path,
            media_url: r.media_url || "",
            media_base_url: r.media_base_url || "",
            media_filename: r.media_filename || "",
            mime: r.mime || "",
            kind: r.kind || "",
          }));
        void Promise.resolve(fetchUrl)
          .then((result) => {
            if (result.kind && result.kind !== "model") {
              setUrls(null);
              setError("This file isn’t a supported 3D model.");
              return;
            }
            const mediaUrl = result.media_url || "";
            const base = result.media_base_url || mediaUrl.replace(/[^/]+$/, "");
            const filename = result.media_filename || basename(relativePath);
            if (!mediaUrl || !base) {
              setUrls(null);
              setError("This model can’t be previewed in the panel.");
              return;
            }
            const normalizedBase = base.endsWith("/") ? base : `${base}/`;
            const mime = result.mime || "";
            setUrls((prev) => {
              if (
                prev &&
                prev.media_url === mediaUrl &&
                prev.media_base_url === normalizedBase &&
                prev.media_filename === filename &&
                prev.mime === mime
              ) {
                return prev;
              }
              return {
                media_url: mediaUrl,
                media_base_url: normalizedBase,
                media_filename: filename,
                mime,
              };
            });
          })
          .catch((e: unknown) => {
            setUrls(null);
            setError(e instanceof Error ? e.message : "Failed to load model");
          })
          .finally(() => {
            if (showLoading) setLoading(false);
          });
      });
    },
    [
      relativePath,
      resolvedMediaUrl,
      resolvedMediaBaseUrl,
      resolvedMediaFilename,
      resolvedMediaMime,
    ],
  );

  useEffect(() => {
    const stop = loadMeta();
    return () => stop();
  }, [loadMeta]);

  useWatchProjectFile(
    relativePath,
    () => {
      if (resolvedMedia) return;
      void loadMeta({ showLoading: false });
    },
    { enabled: !loading && !resolvedMedia },
  );

  useEffect(() => {
    if (!urls || !hostRef.current) return;

    const host = hostRef.current;
    let disposed = false;
    let frame = 0;
    let renderer: THREE.WebGLRenderer | null = null;

    const width = Math.max(host.clientWidth, 1);
    const height = Math.max(host.clientHeight, 1);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1d23);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
    camera.position.set(2, 2, 4);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPixelRatio));
    renderer.setSize(width, height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.replaceChildren(renderer.domElement);

    const hemi = new THREE.HemisphereLight(0xffffff, 0x444466, LIGHT_DEFAULTS.ambient);
    scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, LIGHT_DEFAULTS.key);
    positionKeyLight(key, LIGHT_DEFAULTS.azimuth, LIGHT_DEFAULTS.elevation);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x8899aa, LIGHT_DEFAULTS.fill);
    fill.position.set(-4, 2, -3);
    scene.add(fill);
    const grid = new THREE.GridHelper(10, 10, 0x3a3f4b, 0x2a2e36);
    scene.add(grid);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const handle: SceneHandle = {
      scene,
      camera,
      controls,
      grid,
      root: new THREE.Group(),
      hemi,
      key,
      fill,
      mixer: null,
      clips: [],
      action: null,
      sphere: null,
      skeleton: null,
      lastTime: performance.now() / 1000,
      speed: 1,
      loop: true,
      scrubbing: false,
    };
    handleRef.current = handle;

    setStatus("Loading mesh…");
    const suffix = suffixOf(urls.media_filename || relativePath);

    void loadModel(suffix, urls.media_base_url, urls.media_filename)
      .then(({ object, animations }) => {
        if (disposed) return;
        handle.root = object;
        scene.add(object);
        fitCameraToObject(camera, object, controls);

        const mats = collectMaterials(object);
        setMaterials(mats);

        const clips = animations.filter((c) => c && c.duration > 0);
        handle.clips = clips;
        setClipNames(clips.map((c, i) => c.name?.trim() || `Clip ${i + 1}`));
        if (clips.length > 0) {
          handle.mixer = new THREE.AnimationMixer(object);
          const action = handle.mixer.clipAction(clips[0]);
          action.setLoop(THREE.LoopRepeat, Infinity);
          action.clampWhenFinished = false;
          action.reset().play();
          handle.action = action;
          setActiveClip(0);
          setPlaying(true);
          setClipDuration(clips[0].duration);
        }

        const matLabel =
          mats.length === 0 ? "No materials" : mats.length === 1 ? "1 material" : `${mats.length} materials`;
        const animLabel = clips.length === 0 ? "" : clips.length === 1 ? " · animated" : ` · ${clips.length} clips`;
        setStatus(`Orbit/zoom · ${matLabel}${animLabel}`);
        setError(null);
      })
      .catch((e: unknown) => {
        if (disposed) return;
        setError(e instanceof Error ? e.message : "Failed to parse 3D model");
        setStatus("");
        setMaterials([]);
        setClipNames([]);
      });

    const onResize = () => {
      if (!renderer) return;
      const w = Math.max(host.clientWidth, 1);
      const h = Math.max(host.clientHeight, 1);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(host);

    const syncPause = () => {
      const hiddenDoc = typeof document !== "undefined" && document.visibilityState === "hidden";
      const offscreen = host.clientWidth < 2 || host.clientHeight < 2;
      pauseRef.current = hiddenDoc || offscreen;
    };
    const onVisibility = () => syncPause();
    document.addEventListener("visibilitychange", onVisibility);
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((e) => e.isIntersecting && e.intersectionRatio > 0);
        pauseRef.current = !visible || document.visibilityState === "hidden";
      },
      { threshold: 0.01 },
    );
    io.observe(host);
    syncPause();

    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (pauseRef.current) {
        handle.lastTime = performance.now() / 1000;
        return;
      }
      const now = performance.now() / 1000;
      const delta = Math.min(Math.max(now - handle.lastTime, 0), 0.1);
      handle.lastTime = now;
      const h = handleRef.current;
      if (h?.mixer && h.action && !h.scrubbing) {
        h.mixer.update(delta * h.speed);
      }
      if (h?.action && scrubRef.current && timeLabelRef.current && !h.scrubbing) {
        const dur = h.action.getClip().duration || 0;
        const t = h.action.time;
        if (dur > 0) {
          scrubRef.current.max = String(dur);
          scrubRef.current.value = String(t);
        }
        timeLabelRef.current.textContent = `${formatAnimTime(t)} / ${formatAnimTime(dur)}`;
      }
      controls.update();
      renderer?.render(scene, camera);
    };
    tick();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
      io.disconnect();
      ro.disconnect();
      controls.dispose();
      handle.mixer?.stopAllAction();
      if (handle.skeleton) {
        handle.scene.remove(handle.skeleton);
        handle.skeleton = null;
      }
      if (handle.sphere) {
        handle.scene.remove(handle.sphere);
        handle.sphere.geometry.dispose();
        const sm = handle.sphere.material;
        if (!Array.isArray(sm)) sm.dispose();
      }
      handle.root.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry?.dispose();
          const mat = mesh.material;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose?.();
        }
      });
      handleRef.current = null;
      renderer?.dispose();
      host.replaceChildren();
    };
  }, [urls?.media_url, urls?.media_base_url, urls?.media_filename, relativePath, maxPixelRatio]);

  // Model highlight (only in model mode)
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.root || previewMode !== "model") return;
    applyMaterialHighlight(h.root, selectedMatId);
  }, [selectedMatId, materials, previewMode]);

  // Material-only sphere mode
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.root) return;

    const clearSphere = () => {
      if (!h.sphere) return;
      h.scene.remove(h.sphere);
      h.sphere.geometry.dispose();
      const sm = h.sphere.material;
      if (!Array.isArray(sm)) sm.dispose();
      h.sphere = null;
    };

    if (previewMode !== "material" || !selectedMatId) {
      clearSphere();
      h.root.visible = true;
      h.grid.visible = showGrid;
      if (previewMode === "model") {
        fitCameraToObject(h.camera, h.root, h.controls);
      }
      return;
    }

    const source = findMaterialById(h.root, selectedMatId);
    if (!source) return;

    clearMaterialDims(h.root);
    h.root.visible = false;
    h.grid.visible = false;
    clearSphere();

    const mat = source.clone();
    mat.side = THREE.FrontSide;
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 64), mat);
    h.sphere = sphere;
    h.scene.add(sphere);
    fitCameraToObject(h.camera, sphere, h.controls);
  }, [previewMode, selectedMatId, materials, showGrid]);

  // Switch animation clip (reset to start of new clip)
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.mixer || h.clips.length === 0) return;
    const clip = h.clips[activeClip];
    if (!clip) return;
    h.mixer.stopAllAction();
    const action = h.mixer.clipAction(clip);
    action.setLoop(h.loop ? THREE.LoopRepeat : THREE.LoopOnce, h.loop ? Infinity : 1);
    action.clampWhenFinished = !h.loop;
    action.reset();
    action.play();
    action.paused = !playing;
    h.action = action;
    setClipDuration(clip.duration);
  }, [activeClip, clipNames]);

  // Play / pause without resetting
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.action) return;
    h.action.paused = !playing;
    if (playing && !h.action.isRunning()) h.action.play();
  }, [playing]);

  // Loop + speed
  useEffect(() => {
    const h = handleRef.current;
    if (!h) return;
    h.loop = loop;
    h.speed = speed;
    if (h.action) {
      h.action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
      h.action.clampWhenFinished = !loop;
    }
  }, [loop, speed]);

  // Skeleton helper for testing joints
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.root) return;
    if (h.skeleton) {
      h.scene.remove(h.skeleton);
      h.skeleton = null;
    }
    if (!showSkeleton || previewMode !== "model") return;
    const helper = new THREE.SkeletonHelper(h.root);
    helper.visible = true;
    h.skeleton = helper;
    h.scene.add(helper);
    return () => {
      if (h.skeleton) {
        h.scene.remove(h.skeleton);
        h.skeleton = null;
      }
    };
  }, [showSkeleton, previewMode, clipNames]);

  useEffect(() => {
    const h = handleRef.current;
    if (!h?.grid) return;
    if (previewMode === "material") {
      h.grid.visible = false;
      return;
    }
    h.grid.visible = showGrid;
  }, [showGrid, previewMode, urls]);

  // Light intensities + key light direction
  useEffect(() => {
    const h = handleRef.current;
    if (!h) return;
    h.hemi.intensity = ambientIntensity;
    h.key.intensity = keyIntensity;
    h.fill.intensity = fillIntensity;
    positionKeyLight(h.key, keyAzimuth, keyElevation);
  }, [ambientIntensity, keyIntensity, fillIntensity, keyAzimuth, keyElevation, urls]);

  // Auto-rotate
  useEffect(() => {
    const h = handleRef.current;
    if (!h) return;
    h.controls.autoRotate = autoRotate;
    h.controls.autoRotateSpeed = 2.0;
  }, [autoRotate, urls]);

  // Wireframe
  useEffect(() => {
    const h = handleRef.current;
    if (!h?.root) return;
    applyWireframe(h.root, wireframe);
    if (h.sphere) applyWireframe(h.sphere, wireframe);
  }, [wireframe, previewMode, materials, urls]);

  const restartClip = () => {
    const h = handleRef.current;
    if (!h?.action) return;
    h.action.reset().play();
    h.action.paused = !playing;
    setPlaying(true);
  };

  const onScrubStart = () => {
    const h = handleRef.current;
    if (h) h.scrubbing = true;
  };

  const onScrub = (value: number) => {
    const h = handleRef.current;
    if (!h?.action || !h.mixer) return;
    h.action.time = value;
    h.action.paused = true;
    h.mixer.update(0);
    if (timeLabelRef.current) {
      const dur = h.action.getClip().duration || 0;
      timeLabelRef.current.textContent = `${formatAnimTime(value)} / ${formatAnimTime(dur)}`;
    }
  };

  const onScrubEnd = () => {
    const h = handleRef.current;
    if (!h?.action) return;
    h.scrubbing = false;
    h.action.paused = !playing;
  };

  const selectMaterial = (id: string) => {
    setSelectedMatId((prev) => (prev === id ? null : id));
  };

  const enterMaterialSphere = (id: string) => {
    setSelectedMatId(id);
    setPreviewMode("material");
  };

  const exitMaterialSphere = () => {
    setPreviewMode("model");
  };

  if (loading && !urls) {
    return <div className="ui-status-muted model-preview-loading">Loading model…</div>;
  }

  if (error && !urls) {
    return (
      <div className="file-editor-pane file-editor-pane-layout model-preview-pane">
        <div className="ui-status-error model-preview-toolbar-error">{error}</div>
      </div>
    );
  }

  return (
    <div className="file-editor-pane file-editor-pane-layout model-preview-pane">
      <div className="model-preview-toolbar" role="toolbar" aria-label="Model viewer">
        <div className="model-preview-toolbar-row">
          <div className="model-preview-toolbar-group">
            {previewMode === "material" ? (
              <button type="button" className="settings-btn model-toolbar-btn" onClick={exitMaterialSphere}>
                Back to model
              </button>
            ) : null}
            {materials.length > 0 ? (
              <button
                type="button"
                className={`settings-btn model-toolbar-btn${showMaterials ? " is-active" : ""}`}
                onClick={() => setShowMaterials((v) => !v)}
              >
                Materials{materials.length ? ` (${materials.length})` : ""}
              </button>
            ) : null}
            <button
              type="button"
              className={`settings-btn model-toolbar-btn${showGrid ? " is-active" : ""}`}
              onClick={() => setShowGrid((v) => !v)}
              title="Toggle ground grid"
            >
              Grid
            </button>
            <button
              type="button"
              className={`settings-btn model-toolbar-btn${showLights ? " is-active" : ""}`}
              onClick={() => setShowLights((v) => !v)}
              title="Adjust scene lighting"
            >
              Light
            </button>
            <button
              type="button"
              className={`settings-btn model-toolbar-btn${autoRotate ? " is-active" : ""}`}
              onClick={() => setAutoRotate((v) => !v)}
              title="Auto-rotate the camera"
            >
              Spin
            </button>
            <button
              type="button"
              className={`settings-btn model-toolbar-btn${wireframe ? " is-active" : ""}`}
              onClick={() => setWireframe((v) => !v)}
              title="Toggle wireframe"
            >
              Wire
            </button>
            {clipNames.length > 0 ? (
              <label className="model-anim-check">
                <input
                  type="checkbox"
                  checked={showSkeleton}
                  onChange={(e) => setShowSkeleton(e.target.checked)}
                />
                Skeleton
              </label>
            ) : null}
          </div>
          {clipNames.length > 0 ? (
            <div className="model-preview-toolbar-group model-preview-toolbar-anim">
              <button
                type="button"
                className="settings-btn model-toolbar-btn"
                onClick={() => setPlaying((p) => !p)}
              >
                {playing ? "Pause" : "Play"}
              </button>
              <button type="button" className="settings-btn model-toolbar-btn" onClick={restartClip}>
                Restart
              </button>
              <label className="model-anim-label">
                Clip
                <ChoiceDropdown
                  className="model-anim-select"
                  size="compact"
                  mode="radio"
                  aria-label="Animation clip"
                  value={String(activeClip)}
                  options={clipNames.map((name, i) => ({
                    value: String(i),
                    label: name,
                  }))}
                  onChange={(next) => setActiveClip(Number(next))}
                />
              </label>
              <label className="model-anim-label">
                Speed
                <ChoiceDropdown
                  className="model-anim-select model-anim-select--narrow"
                  size="compact"
                  mode="radio"
                  aria-label="Playback speed"
                  value={String(speed)}
                  options={[
                    { value: "0.25", label: "0.25×" },
                    { value: "0.5", label: "0.5×" },
                    { value: "1", label: "1×" },
                    { value: "1.5", label: "1.5×" },
                    { value: "2", label: "2×" },
                  ]}
                  onChange={(next) => setSpeed(Number(next))}
                />
              </label>
              <label className="model-anim-check">
                <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
                Loop
              </label>
            </div>
          ) : null}
          {toolbarExtras ? <div className="model-preview-toolbar-extras">{toolbarExtras}</div> : null}
        </div>
        {clipNames.length > 0 ? (
          <div className="model-anim-scrub-row">
            <input
              ref={scrubRef}
              type="range"
              className="model-anim-scrub"
              min={0}
              max={clipDuration || 1}
              step={0.01}
              defaultValue={0}
              onPointerDown={onScrubStart}
              onPointerUp={onScrubEnd}
              onChange={(e) => onScrub(Number(e.target.value))}
              aria-label="Animation timeline"
            />
            <span ref={timeLabelRef} className="model-anim-time">
              0.00s / {formatAnimTime(clipDuration)}
            </span>
          </div>
        ) : null}
        {error ? <div className="ui-status-error model-preview-toolbar-error">{error}</div> : null}
      </div>
      <div className="model-preview-body">
        <div ref={hostRef} className="model-preview-canvas-host" />
        {showLights ? (
          <aside className="model-lights-panel" aria-label="Lighting">
            <div className="model-lights-title">
              <span>Lighting</span>
              <button
                type="button"
                className="settings-btn model-lights-reset"
                onClick={() => {
                  setAmbientIntensity(LIGHT_DEFAULTS.ambient);
                  setKeyIntensity(LIGHT_DEFAULTS.key);
                  setFillIntensity(LIGHT_DEFAULTS.fill);
                  setKeyAzimuth(LIGHT_DEFAULTS.azimuth);
                  setKeyElevation(LIGHT_DEFAULTS.elevation);
                }}
              >
                Reset
              </button>
            </div>
            <label className="model-light-row">
              <span className="model-light-label">Ambient</span>
              <input
                type="range"
                min={0}
                max={3}
                step={0.05}
                value={ambientIntensity}
                onChange={(e) => setAmbientIntensity(Number(e.target.value))}
              />
              <span className="model-light-value">{ambientIntensity.toFixed(2)}</span>
            </label>
            <label className="model-light-row">
              <span className="model-light-label">Key</span>
              <input
                type="range"
                min={0}
                max={5}
                step={0.05}
                value={keyIntensity}
                onChange={(e) => setKeyIntensity(Number(e.target.value))}
              />
              <span className="model-light-value">{keyIntensity.toFixed(2)}</span>
            </label>
            <label className="model-light-row">
              <span className="model-light-label">Fill</span>
              <input
                type="range"
                min={0}
                max={3}
                step={0.05}
                value={fillIntensity}
                onChange={(e) => setFillIntensity(Number(e.target.value))}
              />
              <span className="model-light-value">{fillIntensity.toFixed(2)}</span>
            </label>
            <label className="model-light-row">
              <span className="model-light-label">Rotation</span>
              <input
                type="range"
                min={0}
                max={360}
                step={1}
                value={keyAzimuth}
                onChange={(e) => setKeyAzimuth(Number(e.target.value))}
              />
              <span className="model-light-value">{Math.round(keyAzimuth)}°</span>
            </label>
            <label className="model-light-row">
              <span className="model-light-label">Height</span>
              <input
                type="range"
                min={5}
                max={89}
                step={1}
                value={keyElevation}
                onChange={(e) => setKeyElevation(Number(e.target.value))}
              />
              <span className="model-light-value">{Math.round(keyElevation)}°</span>
            </label>
          </aside>
        ) : null}
        {showMaterials && materials.length > 0 ? (
          <aside className="model-materials-panel" aria-label="Materials">
            <div className="model-materials-title">
              {previewMode === "material" ? "Material preview" : "Materials"}
            </div>
            <ul className="model-materials-list">
              {materials.map((mat) => {
                const selected = selectedMatId === mat.id;
                return (
                  <li key={mat.id}>
                    <div className={`model-material-card${selected ? " is-selected" : ""}`}>
                      <button
                        type="button"
                        className="model-material-main-btn"
                        onClick={() => selectMaterial(mat.id)}
                        title={selected ? "Clear highlight" : "Highlight on model"}
                      >
                        <span className="model-material-card-inner">
                          <MaterialSwatch colorHex={mat.colorHex} />
                          <span className="model-material-info">
                            <span className="model-material-name">{mat.name}</span>
                            <span className="model-material-meta">
                              {mat.type}
                              {mat.meshCount > 1 ? ` · ${mat.meshCount} meshes` : ""}
                              {mat.colorHex ? ` · ${mat.colorHex}` : ""}
                            </span>
                            {(mat.metalness != null || mat.roughness != null || mat.opacity != null) && (
                              <span className="model-material-meta">
                                {mat.metalness != null ? `metal ${mat.metalness.toFixed(2)}` : ""}
                                {mat.metalness != null && mat.roughness != null ? " · " : ""}
                                {mat.roughness != null ? `rough ${mat.roughness.toFixed(2)}` : ""}
                                {(mat.metalness != null || mat.roughness != null) &&
                                mat.opacity != null &&
                                mat.opacity < 1
                                  ? " · "
                                  : ""}
                                {mat.opacity != null && mat.opacity < 1
                                  ? `α ${mat.opacity.toFixed(2)}`
                                  : ""}
                              </span>
                            )}
                            {mat.maps.length > 0 ? (
                              <span className="model-material-maps">{mat.maps.join(" · ")}</span>
                            ) : (
                              <span className="model-material-maps">No textures</span>
                            )}
                          </span>
                        </span>
                      </button>
                      <button
                        type="button"
                        className="settings-btn model-material-sphere-btn"
                        onClick={() => enterMaterialSphere(mat.id)}
                        title="Preview this material alone on a sphere"
                      >
                        Sphere
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
            {selectedMatId && previewMode === "model" ? (
              <button
                type="button"
                className="settings-btn model-materials-clear"
                onClick={() => setSelectedMatId(null)}
              >
                Clear highlight
              </button>
            ) : null}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
