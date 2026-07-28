/** Deterministic file-kind classification mirrored from file_kinds.py. */

export type FileKind = "text" | "image" | "model" | "audio" | "video" | "unreal_asset" | "binary";

const IMAGE_SUFFIXES = [
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".bmp",
  ".webp",
  ".ico",
  ".svg",
] as const;

const MODEL_SUFFIXES = [".fbx", ".glb", ".gltf", ".obj", ".stl", ".ply", ".dae"] as const;

const AUDIO_SUFFIXES = [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"] as const;

const VIDEO_SUFFIXES = [".mp4", ".webm", ".mov", ".m4v", ".ogv"] as const;

const UNSUPPORTED_IMAGE_SUFFIXES = [".tga", ".psd", ".tif", ".tiff", ".exr", ".hdr"] as const;

const UNREAL_ASSET_SUFFIXES = [
  ".umap",
  ".uasset",
  ".ubulk",
  ".uexp",
  ".uptod",
  ".udic",
  ".ufont",
  ".ushaderbytecode",
  ".upipelinecache",
] as const;

const BINARY_NON_IMAGE_SUFFIXES = [
  ".exe",
  ".dll",
  ".pdb",
  ".zip",
  ".7z",
  ".rar",
  ".pak",
  ".bin",
  ".dat",
  ".ttf",
  ".otf",
  ".woff",
  ".woff2",
  ...UNSUPPORTED_IMAGE_SUFFIXES,
] as const;

export const EDITABLE_TEXT_EXTENSIONS_FULL = [
  ".verse",
  ".versetest",
  ".vson",
  ".py",
  ".txt",
  ".json",
  ".md",
  ".cfg",
  ".ini",
  ".toml",
  ".yaml",
  ".yml",
  ".xml",
  ".csv",
  ".log",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".css",
  ".scss",
  ".less",
  ".html",
  ".htm",
  ".rs",
  ".php",
  ".c",
  ".h",
  ".cpp",
  ".cxx",
  ".cc",
  ".hpp",
  ".hh",
  ".go",
  ".gitignore",
  ".gitattributes",
  ".editorconfig",
  ".dockerignore",
  ".env",
  ".htaccess",
  ".npmrc",
  ".prettierrc",
  ".eslintrc",
] as const;

const KNOWN_TEXT_FILENAMES = new Set([
  ".gitignore",
  ".gitattributes",
  ".editorconfig",
  ".dockerignore",
  ".env",
  ".env.local",
  ".env.development",
  ".env.production",
  ".npmrc",
  ".prettierrc",
  ".eslintrc",
  ".htaccess",
  "dockerfile",
  "makefile",
  "gnumakefile",
  "cmakelists.txt",
  "license",
  "licence",
  "copying",
  "readme",
  "readme.md",
  "readme.txt",
  "authors",
  "contributors",
  "changelog",
  "changes",
  "todo",
  "gemfile",
  "rakefile",
  "procfile",
  "vagrantfile",
  "jenkinsfile",
  "pipfile",
  "poetry.lock",
  "package-lock.json",
  "yarn.lock",
  "pnpm-lock.yaml",
]);

const STEM_TEXT_NAMES = new Set([
  "readme",
  "license",
  "licence",
  "copying",
  "authors",
  "changelog",
  "changes",
  "todo",
]);

function fileBasename(path: string): string {
  const norm = path.replace(/\\/g, "/");
  let rest = norm;
  const lower = norm.toLowerCase();
  if (lower.startsWith("abs:") || lower.startsWith("ext:")) {
    rest = norm.slice(4);
  } else if (lower.startsWith("ws:")) {
    const after = norm.slice(3);
    const slash = after.indexOf("/");
    rest = slash >= 0 ? after.slice(slash + 1) : after;
  }
  const parts = rest.split("/").filter(Boolean);
  return parts[parts.length - 1] || rest;
}

function suffixOf(name: string): string {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  // Dotfiles like .gitignore: treat the whole name as the suffix when there is
  // no stem (matches pathlib Path(".gitignore").suffix).
  if (dot <= 0) {
    return dot === 0 ? lower : "";
  }
  return lower.slice(dot);
}

export function isKnownTextFilename(name: string): boolean {
  const base = name.split(/[/\\]/).pop() || name;
  const lower = base.toLowerCase();
  if (KNOWN_TEXT_FILENAMES.has(lower)) return true;
  const stemDot = lower.lastIndexOf(".");
  const stem = stemDot > 0 ? lower.slice(0, stemDot) : lower;
  if (STEM_TEXT_NAMES.has(stem)) return true;
  if (
    [
      "dockerfile",
      "makefile",
      "gnumakefile",
      "gemfile",
      "rakefile",
      "procfile",
      "vagrantfile",
      "jenkinsfile",
      "pipfile",
    ].includes(lower)
  ) {
    return true;
  }
  return false;
}

export function isImageFilePath(relativePath: string): boolean {
  const name = fileBasename(relativePath);
  return (IMAGE_SUFFIXES as readonly string[]).includes(suffixOf(name));
}

export function isModelFilePath(relativePath: string): boolean {
  const name = fileBasename(relativePath);
  return (MODEL_SUFFIXES as readonly string[]).includes(suffixOf(name));
}

export function isAudioFilePath(relativePath: string): boolean {
  const name = fileBasename(relativePath);
  return (AUDIO_SUFFIXES as readonly string[]).includes(suffixOf(name));
}

export function isVideoFilePath(relativePath: string): boolean {
  const name = fileBasename(relativePath);
  return (VIDEO_SUFFIXES as readonly string[]).includes(suffixOf(name));
}

export function isUnrealAssetPath(relativePath: string): boolean {
  const name = fileBasename(relativePath);
  return (UNREAL_ASSET_SUFFIXES as readonly string[]).includes(suffixOf(name));
}

export function classifyFilePath(relativePath: string): FileKind {
  const name = fileBasename(relativePath);
  const suffix = suffixOf(name);
  if ((UNREAL_ASSET_SUFFIXES as readonly string[]).includes(suffix)) return "unreal_asset";
  if ((IMAGE_SUFFIXES as readonly string[]).includes(suffix)) return "image";
  if ((MODEL_SUFFIXES as readonly string[]).includes(suffix)) return "model";
  if ((AUDIO_SUFFIXES as readonly string[]).includes(suffix)) return "audio";
  if ((VIDEO_SUFFIXES as readonly string[]).includes(suffix)) return "video";
  if ((BINARY_NON_IMAGE_SUFFIXES as readonly string[]).includes(suffix)) return "binary";
  if (isKnownTextFilename(name) || (EDITABLE_TEXT_EXTENSIONS_FULL as readonly string[]).includes(suffix)) {
    return "text";
  }
  // Extensionless / unknown — prefer text; backend sniff corrects binaries.
  return "text";
}

export function isEditableTextFilePath(relativePath: string): boolean {
  return classifyFilePath(relativePath) === "text";
}

/** Monaco language id for a path (dotfiles → plaintext). */
export function monacoLanguageForFileKind(relativePath: string): string | null {
  const name = fileBasename(relativePath).toLowerCase();
  if (name === ".gitignore" || name.endsWith(".gitignore")) return "plaintext";
  if (name === "dockerfile" || name.startsWith("dockerfile.")) return "plaintext";
  if (name === "makefile" || name === "gnumakefile") return "plaintext";
  return null;
}
