import { describe, expect, it } from "vitest";

import {
  canOfferMaterialPreview,
  canOfferStaticMeshPreview,
  canOfferTexturePreview,
  cleanPreviewError,
  guessPreviewKind,
  meshPreviewMediaFromResult,
} from "./meshPreviewHelpers";

describe("guessPreviewKind", () => {
  it("classifies by asset class", () => {
    expect(guessPreviewKind("x.uasset", "StaticMesh")).toBe("static_mesh");
    expect(guessPreviewKind("x.uasset", "Material")).toBe("material");
    expect(guessPreviewKind("x.uasset", "Texture2D")).toBe("texture");
    expect(guessPreviewKind("x.uasset", "NiagaraEmitter")).toBe("niagara");
  });

  it("classifies by path heuristics", () => {
    expect(guessPreviewKind("Content/PickupSet/Materials/M_Ground.uasset")).toBe("material");
    expect(guessPreviewKind("Content/Textures/T_Bronze_2.uasset")).toBe("texture");
    expect(guessPreviewKind("Content/PickupSet/Fx/NiagaraEmitters/Ne_Aura.uasset")).toBe("niagara");
    expect(guessPreviewKind("Content/Meshes/SM_Box.uasset")).toBe("static_mesh");
  });

  it("ignores ObjectRedirector class and uses path", () => {
    expect(
      guessPreviewKind("Content/sA_PickupSet_1/Models/SM_Pickup_Gem.uasset", "ObjectRedirector"),
    ).toBe("static_mesh");
    expect(
      canOfferStaticMeshPreview(
        "Content/sA_PickupSet_1/Models/SM_Pickup_Gem.uasset",
        undefined,
        "ObjectRedirector",
      ),
    ).toBe(true);
  });

  it("does not treat textures as meshes even with stale mesh kind", () => {
    expect(
      canOfferStaticMeshPreview("Content/Textures/T_Bronze_2.uasset", true, "Texture2D", "static_mesh"),
    ).toBe(false);
    expect(canOfferTexturePreview("Content/Textures/T_Bronze_2.uasset", true, "Texture2D")).toBe(true);
  });
});

describe("canOfferStaticMeshPreview", () => {
  it("allows only static meshes", () => {
    expect(canOfferStaticMeshPreview("Content/Meshes/SM_Box.uasset")).toBe(true);
    expect(canOfferStaticMeshPreview("Content/Maps/Island.umap")).toBe(false);
    expect(canOfferStaticMeshPreview("Content/Meshes/box.fbx")).toBe(false);
    expect(canOfferStaticMeshPreview("Content/Materials/M_Ground.uasset")).toBe(false);
    expect(canOfferStaticMeshPreview("Content/Fx/Ne_Aura.uasset", true, "NiagaraEmitter")).toBe(false);
  });

  it("respects supports_mesh_preview flag", () => {
    expect(canOfferStaticMeshPreview("Content/Meshes/SM_Box.uasset", false)).toBe(false);
    expect(canOfferStaticMeshPreview("Content/Meshes/SM_Box.uasset", true)).toBe(true);
  });
});

describe("canOfferMaterialPreview", () => {
  it("allows materials only", () => {
    expect(canOfferMaterialPreview("Content/Materials/M_Ground.uasset")).toBe(true);
    expect(canOfferMaterialPreview("Content/Meshes/SM_Box.uasset")).toBe(false);
    expect(canOfferMaterialPreview("Content/Fx/Ne_Aura.uasset")).toBe(false);
  });
});

describe("cleanPreviewError", () => {
  it("strips traceback and command wrappers", () => {
    expect(
      cleanPreviewError(
        "UEFN command 'preview_static_mesh' failed: Not a StaticMesh (got Material).\nTraceback (most recent call last):\n  File ...",
      ),
    ).toBe("Not a StaticMesh (got Material).");
  });
});

describe("meshPreviewMediaFromResult", () => {
  it("returns null for failures / incomplete payloads", () => {
    expect(meshPreviewMediaFromResult({ ok: false, error: "nope" })).toBeNull();
    expect(
      meshPreviewMediaFromResult({
        ok: true,
        media_url: "http://127.0.0.1:4199/mesh-previews/abc/model.fbx",
      }),
    ).toBeNull();
  });

  it("normalizes media_base_url trailing slash", () => {
    const media = meshPreviewMediaFromResult({
      ok: true,
      media_url: "http://127.0.0.1:4199/mesh-previews/abc/model.fbx",
      media_base_url: "http://127.0.0.1:4199/mesh-previews/abc",
      media_filename: "model.fbx",
      mime: "model/fbx",
    });
    expect(media?.media_base_url).toBe("http://127.0.0.1:4199/mesh-previews/abc/");
    expect(media?.media_filename).toBe("model.fbx");
  });
});
