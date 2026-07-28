/**
 * Board iframe → chat pings for Duck-Tac-Toe.
 * Queue when the agent is already running so mid-think clicks are not lost.
 */
import { getApi } from "../hooks/usePanelApi";
import { enqueuePrompt, makeQueuedPrompt } from "../hooks/promptQueue";
import type { ChatTab } from "../types/panel";
import { BRIDGE_CHANNEL } from "./constants";

export const DUCKTACTOE_REMATCH_PROMPT =
  "New game on the board — I'm X (I open), you're O. Call ducktactoe_state and play when it's your turn.";

export const DUCKTACTOE_YOUR_MOVE_PROMPT =
  "I moved on the board. Call ducktactoe_state and play if it's your turn. Never claim a win without a successful ducktactoe_move result.";

export const DUCKTACTOE_GAME_OVER_PROMPT =
  "Duck-Tac-Toe board says game over (not UEFN / not the island). Call ducktactoe_state, congratulate or note the draw, offer rematch. Do not grep Verse or invent a roguelike win.";

type PingOpts = {
  chat: ChatTab;
  isRunning: () => boolean;
};

export function subscribeDucktactoeBoardPings(opts: PingOpts): () => void {
  let pinging = false;

  const resolveModel = async (): Promise<string> => {
    const api = getApi();
    let model = (opts.chat.model || "").trim();
    if (!model && api?.get_settings) {
      const settings = await api.get_settings();
      model = String(settings?.default_model || settings?.agent_model || "").trim();
    }
    return model;
  };

  const ping = (prompt: string) => {
    if (pinging) return;
    const api = getApi();
    if (!api?.send_message) return;
    pinging = true;
    void (async () => {
      try {
        const model = await resolveModel();
        if (!model) return;
        if (opts.isRunning()) {
          const item = makeQueuedPrompt(prompt, { mode: "agent", model });
          if (item) enqueuePrompt(opts.chat.id, item);
          return;
        }
        await api.send_message(opts.chat.id, prompt, "agent", model);
      } catch {
        /* host may reject — board state still applied */
      } finally {
        pinging = false;
      }
    })();
  };

  const onMsg = (ev: MessageEvent) => {
    const d = ev.data;
    if (!d || d.channel !== BRIDGE_CHANNEL) return;
    if (d.event === "ducktactoe.new_game") ping(DUCKTACTOE_REMATCH_PROMPT);
    else if (d.event === "ducktactoe.human_move") {
      const st = d.state;
      const over =
        st &&
        (String(st.status || "") === "over" ||
          Boolean(st.winner) ||
          String(st.winner || "") === "draw");
      ping(over ? DUCKTACTOE_GAME_OVER_PROMPT : DUCKTACTOE_YOUR_MOVE_PROMPT);
    }
  };
  window.addEventListener("message", onMsg);
  return () => window.removeEventListener("message", onMsg);
}
