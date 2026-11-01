import { useEffect } from "react";
import { MarkdownContent } from "../../components/rich-content/MarkdownContent";
import { noteChatRefFaces } from "../../components/chatRefFaces";

export const CHAT_APPEARANCE_PREVIEW = `# Ledger Test Complete
\`ducky run sync --ledger\`

Leaving \`Props\` unwired for now per the lock — **3 of 4 fields** are wired, which is plenty for the ledger exercise. Saving everything and checking the overall ledger. Next pass is [@Animation Artist](profile:animation-artist) with [/Verse](skill:verse).

## Run Summary
- **Editor changes:** 23 applied
- **Blocked:** 4 retries
- **Programs:** UEFN 20 · Blender 2 · Verse 1 · File 1

## Inventory — \`Test/LedgerFull\`
- **Verse device** / \`ledger_full_test_device.verse\` — \`FullTest_Manager\` with 4 \`@editable\`s (\`EntryTrigger\`, \`ToggleButton\`, \`StatusHud\`, prop array). 3 of 4 wired; \`Props\` is stale. See [/epic_mcp](subskill:verse/epic_mcp).
- **Devices** / \`FullTest_EntryTrigger, FullTest_Button, FullTest_HudMessage\` — wired into the Verse device.
- **Random clutter** / \`Scatter_Barrel_01, Scatter_Rock_01\` — plus \`FullTest_Prop_01\` / \`FullTest_Prop_02\` cubes.
- **Blueprint** / \`P_LedgerTest_Prefab\` — blank EntityPrefab at \`/ExampleProject1/Prefabs\`.
- **Blender mesh** / \`SM_LedgerTestCrate\` — modeled in Blender, placed as \`BlenderCrate_01\`.
- **UMG Widget** / \`UW_LedgerTestHud\` — widget at \`/ExampleProject1/UI\` with \`CanvasPanel\` + \`Image\`.

> **Loose end:** The \`Props\` array field on \`FullTest_Manager\` needs another pass once the listener settles. [/UEFN Niagara](mcp:vfx) can place the rest.

## Place and wire
1. \`workspace_write_file\` → \`workspace_list_verse_errors\` → \`workspace_compile_verse\` → \`unreal__call_tool(VerseToolset.VerseToolset, BuildAll)\` with [/Verse](skill:verse).
2. Find it with \`search_assets(search="<class_name>", directory="/ExampleProject1")\` — project mount, not \`/Game\`.
3. \`spawn_actor(asset_path=..., label=..., folder="Test/Ledger")\` then \`set_actor_label\` + \`set_actor_folder\`.
4. Wire \`EntryTrigger\` with \`wire_verse_device_ref(actor_path, field, target_path)\`.

\`\`\`verse
using { /Fortnite.com/Devices }
ledger_test_device := class(creative_device):
    @editable EntryTrigger : trigger_device = trigger_device{}
\`\`\`

> [!TIP] Verified
> \`SM_Chair\` and \`SM_Desk\` imported under \`/ExampleProject1/Meshes\`.

| Result | Detail |
| --- | --- |
| **Compile** | \`workspace_compile_verse\` · [/blender](mcp:blender) |
| **Place** | \`PlaceDevice\` |
`;

export function ChatResponsePreview() {
  useEffect(() => {
    noteChatRefFaces([{ href: "profile:animation-artist", duckyStyle: "artist" }]);
  }, []);
  return (
    <div className="appearance-live-preview appearance-chat-preview-wrap">
      <div className="appearance-live-preview-label">Live preview</div>
      <div className="appearance-chat-preview">
        <MarkdownContent text={CHAT_APPEARANCE_PREVIEW} />
      </div>
    </div>
  );
}
