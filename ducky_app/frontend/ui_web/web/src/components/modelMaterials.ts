import * as THREE from "three";

export type ModelMaterialInfo = {
  id: string;
  name: string;
  type: string;
  colorHex: string | null;
  opacity: number | null;
  metalness: number | null;
  roughness: number | null;
  maps: string[];
  meshCount: number;
};

function colorToHex(color: THREE.Color | undefined | null): string | null {
  if (!color) return null;
  try {
    return `#${color.getHexString()}`;
  } catch {
    return null;
  }
}

function textureLabel(tex: THREE.Texture | null | undefined, slot: string): string | null {
  if (!tex) return null;
  const src =
    (tex as THREE.Texture & { name?: string }).name ||
    (typeof tex.image === "object" && tex.image && "src" in tex.image
      ? String((tex.image as { src?: string }).src || "")
          .split("/")
          .pop()
      : "") ||
    "";
  const short = src ? src.split("?")[0] : "";
  return short ? `${slot}: ${short}` : slot;
}

/** Walk the loaded scene and collect unique materials + which maps they use. */
export function collectMaterials(root: THREE.Object3D): ModelMaterialInfo[] {
  const byKey = new Map<string, ModelMaterialInfo>();

  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of mats) {
      if (!mat) continue;
      const key = mat.uuid;
      const existing = byKey.get(key);
      if (existing) {
        existing.meshCount += 1;
        continue;
      }

      const std = mat as THREE.MeshStandardMaterial;
      const basic = mat as THREE.MeshBasicMaterial;
      const phong = mat as THREE.MeshPhongMaterial;
      const maps: string[] = [];
      for (const [slot, tex] of [
        ["map", std.map ?? basic.map ?? phong.map],
        ["normal", std.normalMap],
        ["roughness", std.roughnessMap],
        ["metalness", std.metalnessMap],
        ["ao", std.aoMap],
        ["emissive", std.emissiveMap ?? phong.emissiveMap],
        ["bump", std.bumpMap ?? phong.bumpMap],
        ["alpha", std.alphaMap ?? basic.alphaMap],
        ["light", std.lightMap],
        ["env", std.envMap],
      ] as const) {
        const label = textureLabel(tex as THREE.Texture | null | undefined, slot);
        if (label) maps.push(label);
      }

      const color =
        "color" in mat && mat.color instanceof THREE.Color ? colorToHex(mat.color) : null;

      byKey.set(key, {
        id: mat.uuid,
        name: mat.name?.trim() || mat.type || "Material",
        type: mat.type,
        colorHex: color,
        opacity: typeof mat.opacity === "number" ? mat.opacity : null,
        metalness: typeof std.metalness === "number" ? std.metalness : null,
        roughness: typeof std.roughness === "number" ? std.roughness : null,
        maps,
        meshCount: 1,
      });
    }
  });

  return Array.from(byKey.values()).sort((a, b) => a.name.localeCompare(b.name));
}
