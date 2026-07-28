import { describe, expect, it } from "vitest";
import * as THREE from "three";

import { collectMaterials } from "./modelMaterials";

describe("collectMaterials", () => {
  it("lists unique materials with color and maps", () => {
    const matA = new THREE.MeshStandardMaterial({
      name: "Body",
      color: 0xff0000,
      metalness: 0.2,
      roughness: 0.5,
    });
    const matB = new THREE.MeshStandardMaterial({
      name: "Trim",
      color: 0x00ff00,
    });
    const tex = new THREE.Texture();
    tex.name = "albedo.png";
    matA.map = tex;

    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.BoxGeometry(), matA));
    root.add(new THREE.Mesh(new THREE.BoxGeometry(), matA));
    root.add(new THREE.Mesh(new THREE.BoxGeometry(), matB));

    const mats = collectMaterials(root);
    expect(mats).toHaveLength(2);
    const body = mats.find((m) => m.name === "Body");
    const trim = mats.find((m) => m.name === "Trim");
    expect(body?.colorHex).toBe("#ff0000");
    expect(body?.meshCount).toBe(2);
    expect(body?.maps.some((m) => m.startsWith("map:"))).toBe(true);
    expect(trim?.colorHex).toBe("#00ff00");
    expect(trim?.meshCount).toBe(1);
  });
});
