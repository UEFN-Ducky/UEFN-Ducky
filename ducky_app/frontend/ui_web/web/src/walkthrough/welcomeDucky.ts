/**
 * Finish on the first-run setup: the left dock slides open, the welcome ducky (made after the
 * setup saved the Default Model, so the chat runs on it) opens and types its first message out,
 * then the first-run tour walks through the chat. The message is written ahead of time: no
 * model call, no tokens. Replying to it is a normal chat on the Default Model.
 * The tour's step is kept on the backend, so closing Ducky part way resumes it on that step.
 */
import { getApi } from "../hooks/usePanelApi";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { requestOpenDockSide, requestOpenSidebarPanel } from "../navigation/openSidebarPanel";
import { FIRST_RUN_TOUR_ID } from "./firstRunSetup";
import { armIntro, disarmIntro, whenIntroDone } from "./introTyping";
import { whenWalkthroughHydrated } from "./persistence";
import { markTourCompleted, startTour, subscribeWalkthrough } from "./WalkthroughService";

const WELCOME_TITLE = "Welcome to Ducky";

function wait(ms: number): Promise<void> {
  return new Promise((r) => globalThis.setTimeout(r, ms));
}

/** Save where a new user is in the setup or the first-run tour (a step id or a tour step number). */
export function saveFirstRunProgress(step: string): void {
  void getApi()
    ?.save_first_run_progress?.(step)
    ?.catch(() => {});
}

/**
 * Keeps the first-run tour's step on the backend while it runs. Its end (finished or skipped)
 * is the tour's completion flag the walkthrough saves anyway: the backend reads that as done,
 * so no second write races it.
 */
function trackFirstRunTour(): void {
  let seen = false;
  let last = "";
  let unsub: () => void = () => {};
  unsub = subscribeWalkthrough((s) => {
    if (s.tourId === FIRST_RUN_TOUR_ID) {
      seen = true;
      const step = String(s.stepIndex);
      if (step !== last) saveFirstRunProgress((last = step));
      return;
    }
    if (seen) unsub();
  });
}

async function openWelcomeChat(id: string, title: string, tries: number): Promise<void> {
  // The new chat opens on its own when Ducky announces it; ask too, in case that was missed.
  for (let i = 0; i < tries; i++) {
    if (requestOpenChatTab(id, title)) break;
    await wait(200);
  }
}

async function runFirstRunTour(at: number): Promise<void> {
  // The first-run tour walks through the chat box, so the chat's own tour is covered.
  markTourCompleted("chat.composer");
  await wait(250);
  trackFirstRunTour();
  await startTour(FIRST_RUN_TOUR_ID, { force: true, at });
}

/** Runs once the setup closes; resolves when the first-run tour has started (or there is none). */
export async function welcomeAfterSetup(): Promise<void> {
  requestOpenDockSide("left");
  requestOpenSidebarPanel("chats");
  // The dock slides in before the chat opens next to it.
  await wait(350);
  const api = getApi();
  if (!api?.finish_first_run_setup) return;
  armIntro();
  let made: Awaited<ReturnType<NonNullable<typeof api.finish_first_run_setup>>>;
  try {
    made = await api.finish_first_run_setup();
  } catch {
    made = { ok: false };
  }
  if (!made?.created || !made.id) {
    disarmIntro();
    return;
  }
  await openWelcomeChat(made.id, made.title || WELCOME_TITLE, 10);
  await whenIntroDone();
  await runFirstRunTour(0);
}

/** A restart during the first-run tour: the welcome ducky opens again and the tour goes on at `step`. */
export async function resumeFirstRunTour(chatId: string, step: number): Promise<void> {
  await whenWalkthroughHydrated();
  requestOpenDockSide("left");
  requestOpenSidebarPanel("chats");
  await wait(350);
  if (chatId) await openWelcomeChat(chatId, WELCOME_TITLE, 25);
  await runFirstRunTour(step);
}
