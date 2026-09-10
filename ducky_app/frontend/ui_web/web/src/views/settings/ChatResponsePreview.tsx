import { MarkdownContent } from "../../components/rich-content/MarkdownContent";

export const CHAT_APPEARANCE_PREVIEW = `# Wired the ledger test device
\`workspace_compile_verse\`

## Run Summary
- **Editor Changes**: 4
- **Blocked Ops**: 0
- **Programs**: blender 1, editor 3

## Inventory
- **Verse** / \`ledger_test_device.verse\` — \`FullTest_Manager\` with \`@editable\`s
- **Devices** / \`FullTest_Button\` — \`button_device\` wired to \`ToggleButton\`
- **Prefab** / \`P_LedgerTest_Prefab\` at \`/ExampleProject1/Prefabs\`
- **Mesh** / \`SM_LedgerTestCrate\` in \`COL_Props\`
- **UMG** / \`UW_LedgerTestHud\` — \`CanvasPanel\` + \`Image\`

## Place and wire
1. \`workspace_write_file\` → \`workspace_list_verse_errors\` → \`workspace_compile_verse\` → \`unreal__call_tool(ValkyrieToolset.VerseToolset, BuildAll)\`.
2. Find it with \`search_assets(search="<class_name>", directory="/ExampleProject1")\` — project mount, not \`/Game\`.
3. \`spawn_actor(asset_path=..., label=..., folder="Test/Ledger")\` then \`set_actor_label\` + \`set_actor_folder\`.
4. Wire \`EntryTrigger\` with \`wire_verse_device_ref(actor_path, field, target_path)\`.

\`\`\`verse
using { /Fortnite.com/Devices }
ledger_test_device := class(creative_device):
    @editable EntryTrigger : trigger_device = trigger_device{}
\`\`\`

> **Loose end:** \`Props\` array is still stale — click copies the name.

> [!TIP] Verified
> \`SM_Chair\` and \`SM_Desk\` imported under \`/ExampleProject1/Meshes\`.

| Result | Detail |
| --- | --- |
| **Compile** | \`workspace_compile_verse\` |
| **Place** | \`PlaceDevice\` |
`;

export function ChatResponsePreview() {
  return (
    <div className="appearance-live-preview appearance-chat-preview-wrap">
      <div className="appearance-live-preview-label">Live preview</div>
      <div className="appearance-chat-preview">
        <MarkdownContent text={CHAT_APPEARANCE_PREVIEW} />
      </div>
    </div>
  );
}
