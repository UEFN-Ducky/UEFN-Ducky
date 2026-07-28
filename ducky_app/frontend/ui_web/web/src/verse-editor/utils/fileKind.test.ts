import { describe, expect, it } from "vitest";

import {
  classifyFilePath,
  isAudioFilePath,
  isEditableTextFilePath,
  isImageFilePath,
  isKnownTextFilename,
  isVideoFilePath,
} from "./fileKind";

describe("fileKind", () => {
  it("classifies common text / image / binary names", () => {
    expect(classifyFilePath("Content/.gitignore")).toBe("text");
    expect(classifyFilePath("Makefile")).toBe("text");
    expect(classifyFilePath("Dockerfile")).toBe("text");
    expect(classifyFilePath("LICENSE")).toBe("text");
    expect(classifyFilePath("src/app.ts")).toBe("text");
    expect(classifyFilePath("src/App.tsx")).toBe("text");
    expect(classifyFilePath("lib/main.rs")).toBe("text");
    expect(classifyFilePath("index.php")).toBe("text");
    expect(classifyFilePath("src/main.cpp")).toBe("text");
    expect(classifyFilePath("styles.css")).toBe("text");
    expect(classifyFilePath("shot.png")).toBe("image");
    expect(classifyFilePath("photo.JPEG")).toBe("image");
    expect(classifyFilePath("hero.fbx")).toBe("model");
    expect(classifyFilePath("prop.glb")).toBe("model");
    expect(classifyFilePath("Mesh.uasset")).toBe("unreal_asset");
    expect(classifyFilePath("lib.dll")).toBe("binary");
    expect(classifyFilePath("tex.tga")).toBe("binary");
  });

  it("recognizes known text filenames", () => {
    expect(isKnownTextFilename(".gitignore")).toBe(true);
    expect(isKnownTextFilename("README.md")).toBe(true);
    expect(isKnownTextFilename("random.bin")).toBe(false);
  });

  it("detects image paths including ext: scheme", () => {
    expect(isImageFilePath("Content/Art/icon.webp")).toBe(true);
    expect(isImageFilePath("ext:c:/tmp/photo.jpg")).toBe(true);
    expect(isImageFilePath("Content/Foo.verse")).toBe(false);
  });

  it("treats extensionless unknowns as text candidates", () => {
    expect(classifyFilePath("NOTES")).toBe("text");
    expect(isEditableTextFilePath("NOTES")).toBe(true);
  });

  it("classifies audio suffixes", () => {
    expect(classifyFilePath("theme.mp3")).toBe("audio");
    expect(classifyFilePath("Sfx/hit.WAV")).toBe("audio");
    expect(classifyFilePath("track.flac")).toBe("audio");
    expect(classifyFilePath("voice.m4a")).toBe("audio");
    expect(isAudioFilePath("ext:c:/tmp/song.ogg")).toBe(true);
    expect(isAudioFilePath("Content/Art/icon.webp")).toBe(false);
  });

  it("classifies video suffixes", () => {
    expect(classifyFilePath("intro.mp4")).toBe("video");
    expect(classifyFilePath("Cutscene.WEBM")).toBe("video");
    expect(classifyFilePath("clip.mov")).toBe("video");
    expect(isVideoFilePath("ext:c:/tmp/movie.m4v")).toBe(true);
    expect(isVideoFilePath("theme.mp3")).toBe(false);
  });
});
