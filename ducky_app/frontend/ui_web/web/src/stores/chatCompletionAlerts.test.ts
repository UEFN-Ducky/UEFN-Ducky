import { expect, it, vi } from "vitest";
import {
  dismissCompletionAlert,
  getCompletionAlertChatIds,
  setCompletionAlert,
  subscribeCompletionAlerts,
} from "./chatCompletionAlerts";

it("publishes a new snapshot on every change so useSyncExternalStore re-renders", () => {
  const listener = vi.fn();
  const unsubscribe = subscribeCompletionAlerts(listener);
  const before = getCompletionAlertChatIds();

  setCompletionAlert("chat-1");
  const withAlert = getCompletionAlertChatIds();
  expect(withAlert).not.toBe(before);
  expect(withAlert.has("chat-1")).toBe(true);
  expect(before.has("chat-1")).toBe(false);

  dismissCompletionAlert("chat-1");
  const dismissed = getCompletionAlertChatIds();
  expect(dismissed).not.toBe(withAlert);
  expect(dismissed.has("chat-1")).toBe(false);
  expect(listener).toHaveBeenCalledTimes(2);

  // No-ops keep the snapshot identity (no spurious re-renders).
  dismissCompletionAlert("chat-1");
  setCompletionAlert("");
  expect(getCompletionAlertChatIds()).toBe(dismissed);
  expect(listener).toHaveBeenCalledTimes(2);
  unsubscribe();
});
