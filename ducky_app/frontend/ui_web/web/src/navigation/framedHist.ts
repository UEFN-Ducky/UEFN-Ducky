/** First in-app place replaces the /ducky landing entry; later places push. */
export function framedHistType(stackLen: number): "ud-replace" | "ud-push" {
  return stackLen === 1 ? "ud-replace" : "ud-push";
}
