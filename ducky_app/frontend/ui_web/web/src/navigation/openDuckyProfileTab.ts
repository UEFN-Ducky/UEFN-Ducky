// Pop a library ducky profile into its own editor tab (Duckies detail → Open as tab).

export type OpenDuckyProfileTabRequest = {
  profileId: string;
  name: string;
  duckyStyle?: string;
};

type OpenFn = (req: OpenDuckyProfileTabRequest) => void;

let opener: OpenFn | null = null;
let lastRequestAt = 0;

export function registerOpenDuckyProfileTab(fn: OpenFn): () => void {
  opener = fn;
  return () => {
    if (opener === fn) opener = null;
  };
}

export function requestOpenDuckyProfileTab(req: OpenDuckyProfileTabRequest): void {
  const now = Date.now();
  if (now - lastRequestAt < 400) return;
  lastRequestAt = now;
  opener?.(req);
}
