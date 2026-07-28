import { beforeEach, describe, expect, it } from "vitest";
import {
  invalidateSkillPacksCatalogCache,
  peekSkillPacksCatalogCache,
  rememberSkillPackFiles,
  rememberSkillPacksCatalog,
} from "./skillPacksCatalogCache";
import type { PackWithFiles } from "../skill-pack-studio/model/types";

function pack(id: string, content = ""): PackWithFiles {
  return {
    id,
    label: id,
    description: "",
    kind: "skill",
    version: 1,
    license: "",
    author: "",
    copyright: "",
    homepage: "",
    contact: "",
    allowRedistribute: null,
    sourcePluginId: "",
    source: "",
    storeSlug: "",
    origin: "",
    files: [
      {
        id: "root",
        file: "SKILL.md",
        title: id,
        description: "",
        loadCondition: "",
        content,
        defaultEnabled: true,
        alwaysOn: false,
        origin: "",
      },
    ],
  };
}

describe("skillPacksCatalogCache", () => {
  beforeEach(() => {
    invalidateSkillPacksCatalogCache();
  });

  it("remembers packs across peek", () => {
    rememberSkillPacksCatalog([pack("uefn")], {});
    expect(peekSkillPacksCatalogCache()?.packs[0]?.id).toBe("uefn");
  });

  it("merges loaded file bodies into the cached pack", () => {
    rememberSkillPacksCatalog([pack("uefn")], {});
    rememberSkillPackFiles("uefn", [
      {
        id: "root",
        file: "SKILL.md",
        title: "uefn",
        description: "",
        loadCondition: "",
        content: "# hello",
        defaultEnabled: true,
        alwaysOn: false,
        origin: "",
      },
    ]);
    const cached = peekSkillPacksCatalogCache();
    expect(cached?.contentLoadedPackIds.uefn).toBe(true);
    expect(cached?.packs[0]?.files[0]?.content).toBe("# hello");
  });
});
