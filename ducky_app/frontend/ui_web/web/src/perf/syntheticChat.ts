import type { ChatMessage } from "../types/panel";

const ANSWER = [
  "## Scoring device for turn {t}",
  "",
  "I added **score_device_{t}** and wired it to the timer. Key points:",
  "",
  "- The device subscribes to `TimerDevice.SuccessEvent`",
  "- Score is stored in a `[player]int` map",
  "- UI updates go through `hud_message_device`",
  "",
  "```verse",
  "score_device_{t} := class(creative_device):",
  "    @editable Timer : timer_device = timer_device{}",
  "    var Scores : [player]int = map{}",
  "",
  "    OnBegin<override>()<suspends> : void =",
  "        Timer.SuccessEvent.Subscribe(OnTimerDone)",
  "",
  "    OnTimerDone(Agent : agent) : void =",
  "        if (Player := player[Agent], Current := Scores[Player]):",
  "            if (set Scores[Player] = Current + 10) {}",
  "```",
  "",
  "Next: run a session and confirm the HUD shows the score after the timer fires.",
].join("\n");

/** Deterministic long chat: N turns of user query, 3 tools with 2 KB results, markdown answer. */
export function syntheticMessages(turns: number): ChatMessage[] {
  const out: ChatMessage[] = [];
  let id = 1;
  const result = JSON.stringify({
    ok: true,
    files: Array.from({ length: 40 }, (_, i) => ({ path: `Content/Verse/system_${i}/device_${i}.verse`, size: 1200 + i })),
  });
  for (let t = 0; t < turns; t++) {
    out.push({
      id: id++,
      role: "user",
      text: `Turn ${t}: please add a scoring device and wire it to the timer, then explain the Verse changes.`,
    });
    for (let k = 0; k < 3; k++) {
      const name = k === 0 ? "workspace_read_file" : k === 1 ? "workspace_search" : "workspace_list_dir";
      out.push({
        id: id++,
        role: "tool",
        text: "",
        tool: { name, arguments: { path: `Content/Verse/turn_${t}.verse`, query: "score" }, status: "success" },
      } as ChatMessage);
      out.push({
        id: id++,
        role: "success",
        text: result,
        tool: { name, arguments: {}, status: "success", result },
      } as ChatMessage);
    }
    out.push({ id: id++, role: "assistant", text: ANSWER.split("{t}").join(String(t)) });
  }
  return out;
}
