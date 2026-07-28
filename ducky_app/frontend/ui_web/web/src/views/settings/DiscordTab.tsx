import { useUiTarget } from "../../ui-targets/registry";
import { DiscordBotsCatalog } from "./DiscordBotsCatalog";
import { DiscordCommandsCatalog } from "./DiscordCommandsCatalog";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";
import { Icons } from "../../icons/Icons";

export type DiscordSectionTab = "bots" | "commands";

interface DiscordTabProps {
  sectionTab?: DiscordSectionTab;
}

/** Settings → Discord: Bots catalog or Commands reference (section from header tabs). */
export function DiscordTab({ sectionTab = "bots" }: DiscordTabProps) {
  const rootRef = useUiTarget("settings.discord.root", {
    kind: "settings_field",
    label: "Discord settings",
    route: "settings.discord",
  });
  return (
    <div ref={rootRef} className="discord-settings-tab">
      <div className="general-tab-section-intro discord-settings-walkthrough-intro">
        <h3 className="general-tab-section-title">
          <span className="general-tab-section-icon" aria-hidden>
            <Icons.PanelLeft />
          </span>
          <span className="general-tab-section-title-text">Discord</span>
          <PluginWalkthroughReplayButton pluginId="discord" label="Discord" />
        </h3>
      </div>
      {sectionTab === "commands" ? <DiscordCommandsCatalog /> : <DiscordBotsCatalog />}
    </div>
  );
}
