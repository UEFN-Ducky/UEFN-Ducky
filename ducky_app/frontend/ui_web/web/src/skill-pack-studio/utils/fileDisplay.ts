export function fileBasename(path: string): string {
  const i = path.lastIndexOf("/");
  return i >= 0 ? path.slice(i + 1) : path;
}

export function fileNestDepth(path: string): number {
  return path.includes("/") ? path.split("/").length - 1 : 0;
}
