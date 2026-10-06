import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _resetFirstRunSetupForTests,
  isFirstRunSetupOpen,
  reportFirstRunSetup,
  whenFirstRunSetupDone,
} from "./firstRunSetup";

afterEach(() => {
  _resetFirstRunSetupForTests();
  vi.useRealTimers();
});

async function settled(p: Promise<void>): Promise<boolean> {
  let done = false;
  void p.then(() => {
    done = true;
  });
  await Promise.resolve();
  await Promise.resolve();
  return done;
}

describe("first-run setup gate", () => {
  it("waits while the setup is open and lets the Welcome tour go once it closes", async () => {
    const wait = whenFirstRunSetupDone();
    reportFirstRunSetup(true);
    expect(isFirstRunSetupOpen()).toBe(true);
    expect(await settled(wait)).toBe(false);
    reportFirstRunSetup(false);
    expect(await settled(wait)).toBe(true);
  });

  it("does not wait when the setup was never needed", async () => {
    reportFirstRunSetup(false);
    expect(await settled(whenFirstRunSetupDone())).toBe(true);
  });

  it("gives up waiting for a setup that never reports", async () => {
    vi.useFakeTimers();
    const wait = whenFirstRunSetupDone(1000);
    expect(await settled(wait)).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(await settled(wait)).toBe(true);
  });
});
